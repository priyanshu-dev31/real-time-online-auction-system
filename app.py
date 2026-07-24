from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from functools import wraps
from urllib.parse import urlparse
from uuid import uuid4
import cloudinary
import cloudinary.uploader

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from config import Config
from extension import db
from models.auction import Auction
from models.bid import Bid
from models.user import User


ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB


CLOUDINARY_FOLDER = "auction_images"

cloudinary.config(secure=True)


INDIA_TIMEZONE = ZoneInfo("Asia/Kolkata")


def india_time_to_utc(local_datetime):
    """Convert a datetime entered in Indian time to naive UTC."""

    if local_datetime.tzinfo is None:
        local_datetime = local_datetime.replace(
            tzinfo=INDIA_TIMEZONE
        )

    return local_datetime.astimezone(
        timezone.utc
    ).replace(tzinfo=None)


def utc_now():
    return datetime.now(
        timezone.utc
    ).replace(tzinfo=None)


def utc_to_india_datetime_local(utc_datetime):
    if utc_datetime is None:
        return ""

    utc_datetime = utc_datetime.replace(
        tzinfo=timezone.utc
    )

    india_datetime = utc_datetime.astimezone(
        INDIA_TIMEZONE
    )

    return india_datetime.strftime(
        "%Y-%m-%dT%H:%M"
    )

def utc_to_india_display(utc_datetime):
    if utc_datetime is None:
        return ""

    utc_datetime = utc_datetime.replace(
        tzinfo=timezone.utc
    )

    india_datetime = utc_datetime.astimezone(
        INDIA_TIMEZONE
    )

    return india_datetime.strftime(
        "%d %b %Y, %I:%M %p"
    )


app = Flask(__name__)
app.config.from_object(Config)
@app.template_filter("india_datetime")
def india_datetime_filter(value):
    return utc_to_india_display(value)
csrf = CSRFProtect(app)
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
)
app.config.setdefault("MAX_CONTENT_LENGTH", MAX_IMAGE_SIZE)

upload_folder = app.config.get(
    "UPLOAD_FOLDER",
    os.path.join("static", "uploads"),
)

if not os.path.isabs(upload_folder):
    upload_folder = os.path.join(app.root_path, upload_folder)

app.config["UPLOAD_FOLDER"] = upload_folder
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

db.init_app(app)


# =========================================================
# Authentication helpers
# =========================================================

def login_required(view_function):
    @wraps(view_function)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "danger")
            return redirect(url_for("login"))

        return view_function(*args, **kwargs)

    return wrapped_view


def admin_required(view_function):
    @wraps(view_function)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "danger")
            return redirect(url_for("login"))

        if session.get("role") != "admin":
            flash("You are not authorized to access that page.", "danger")
            return redirect(url_for("dashboard"))

        return view_function(*args, **kwargs)

    return wrapped_view


# =========================================================
# Auction and image helpers
# =========================================================
def close_expired_auction(auction: Auction) -> bool:
    now_utc = datetime.now(
        timezone.utc
    ).replace(tzinfo=None)

    if (
        auction.status == "Active"
        and now_utc >= auction.end_time
    ):
        auction.status = "Closed"
        return True

    return False


def close_all_expired_auctions() -> None:
    expired_auctions = (
        Auction.query
        .filter(
            Auction.status == "Active",
            Auction.end_time <= datetime.now(
                timezone.utc
            ).replace(tzinfo=None)
        )
        .all()
    )

    if not expired_auctions:
        return

    for auction in expired_auctions:
        auction.status = "Closed"

    db.session.commit()


def allowed_image(filename: str) -> bool:
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_IMAGE_EXTENSIONS
    )

def save_uploaded_image(image) -> tuple[str, str]:
    """
    Upload an auction image to Cloudinary.

    Returns:
        secure_url, public_id
    """

    original_filename = secure_filename(
        image.filename or ""
    )

    if not original_filename:
        raise ValueError(
            "Please select a valid image file."
        )

    if not allowed_image(original_filename):
        raise ValueError(
            "Only PNG, JPG, JPEG and WebP images are allowed."
        )

    public_id = f"auction_{uuid4().hex}"

    try:
        image.stream.seek(0)
        upload_result = cloudinary.uploader.upload(
    image.stream,

    # Visible Media Library folder
    asset_folder=CLOUDINARY_FOLDER,

    # Keep the folder in the public ID as well
    public_id=f"{CLOUDINARY_FOLDER}/{public_id}",

    unique_filename=False,
    overwrite=False,
    resource_type="image",
)

    except Exception as error:
        app.logger.exception(
            "Cloudinary image upload failed"
        )

        raise ValueError(
            "The image could not be uploaded. "
            "Please try again."
        ) from error

    secure_url = upload_result.get("secure_url")
    uploaded_public_id = upload_result.get("public_id")

    if not secure_url or not uploaded_public_id:
        raise ValueError(
            "Cloudinary did not return the image URL."
        )

    return secure_url, uploaded_public_id


def delete_uploaded_image(filename: str | None) -> None:
    if not filename:
        return

    image_path = os.path.join(
        app.config["UPLOAD_FOLDER"],
        filename,
    )

    if os.path.isfile(image_path):
        os.remove(image_path)

def get_cloudinary_public_id(
    image_url: str | None,
) -> str | None:
    """
    Get the public ID from an image uploaded by this app.
    External image URLs are ignored.
    """

    if not image_url:
        return None

    parsed_url = urlparse(image_url)

    if parsed_url.netloc.lower() != "res.cloudinary.com":
        return None

    marker = "/image/upload/"

    if marker not in parsed_url.path:
        return None

    cloudinary_path = parsed_url.path.split(
        marker,
        1,
    )[1]

    path_parts = cloudinary_path.split("/")

    # Remove Cloudinary version, for example v1723456789.
    if (
        path_parts
        and path_parts[0].startswith("v")
        and path_parts[0][1:].isdigit()
    ):
        path_parts = path_parts[1:]

    if not path_parts:
        return None

    # Remove file extension from the final part.
    path_parts[-1] = path_parts[-1].rsplit(
        ".",
        1,
    )[0]

    public_id = "/".join(path_parts)

    # Only delete files uploaded inside our folder.
    if not public_id.startswith(
        f"{CLOUDINARY_FOLDER}/"
    ):
        return None

    return public_id


def delete_cloudinary_image(
    public_id: str | None,
) -> None:
    if not public_id:
        return

    try:
        cloudinary.uploader.destroy(
            public_id,
            resource_type="image",
            invalidate=True,
        )

    except Exception:
        app.logger.exception(
            "Cloudinary image deletion failed for %s",
            public_id,
        )

def valid_image_url(value: str) -> bool:
    if not value:
        return True

    parsed_url = urlparse(value)

    return (
        parsed_url.scheme in {"http", "https"}
        and bool(parsed_url.netloc)
    )


def parse_positive_amount(
    raw_value: str,
    field_name: str,
) -> Decimal | None:
    try:
        amount = Decimal(raw_value)
    except (InvalidOperation, TypeError):
        flash(f"{field_name} must be a valid number.", "danger")
        return None

    if not amount.is_finite() or amount <= 0:
        flash(f"{field_name} must be greater than zero.", "danger")
        return None

    return amount.quantize(Decimal("0.01"))


def parse_auction_form(
    require_future_end: bool = True,
) -> dict | None:
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    image_url = request.form.get("image_url", "").strip()

    if not title:
        flash("Auction title is required.", "danger")
        return None

    if not description:
        flash("Auction description is required.", "danger")
        return None

    starting_price = parse_positive_amount(
        request.form.get("starting_price", ""),
        "Starting price",
    )

    if starting_price is None:
        return None

    try:
        start_time_india = datetime.strptime(
            request.form.get("start_time", ""),
            "%Y-%m-%dT%H:%M",
        )

        end_time_india = datetime.strptime(
            request.form.get("end_time", ""),
            "%Y-%m-%dT%H:%M",
        )

    except ValueError:
        flash(
            "Please provide valid start and end times.",
            "danger",
        )
        return None

    start_time = india_time_to_utc(start_time_india)
    end_time = india_time_to_utc(end_time_india)

    if end_time <= start_time:
        flash(
            "End time must be later than the start time.",
            "danger",
        )
        return None

    now_utc = datetime.now(
        timezone.utc
    ).replace(tzinfo=None)

    if require_future_end and end_time <= now_utc:
        flash(
            "End time must be in the future.",
            "danger",
        )
        return None

    if not valid_image_url(image_url):
        flash(
            "Image URL must begin with http:// or https://.",
            "danger",
        )
        return None

    return {
        "title": title,
        "description": description,
        "starting_price": starting_price,
        "start_time": start_time,
        "end_time": end_time,
        "image_url": image_url,
    }


# =========================================================
# Public routes
# =========================================================

@app.route("/")
def home():
    close_all_expired_auctions()
    now = datetime.now(
        timezone.utc
    ).replace(tzinfo=None)

    featured_auctions = (
        Auction.query
        .filter(
            Auction.status == "Active",
            Auction.start_time <= now,
            Auction.end_time > now,
        )
        .order_by(Auction.created_at.desc())
        .limit(3)
        .all()
    )

    return render_template(
        "home.html",
        featured_auctions=featured_auctions,
        total_users=User.query.count(),
        total_auctions=Auction.query.count(),
        total_bids=Bid.query.count(),
    )


@app.route("/register", methods=["GET", "POST"])
@limiter.limit(
    "5 per minute",
    methods=["POST"],
)
def register():
    if "user_id" in session:
        if session.get("role") == "admin":
            return redirect(url_for("admin"))

        return redirect(url_for("dashboard"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not full_name or not email or not password:
            flash("All registration fields are required.", "danger")
            return redirect(url_for("register"))

        if len(password) < 6:
            flash(
                "Password must contain at least 6 characters.",
                "danger",
            )
            return redirect(url_for("register"))

        if User.query.filter_by(email=email).first():
            flash("That email address is already registered.", "danger")
            return redirect(url_for("login"))

        new_user = User(
            full_name=full_name,
            email=email,
            password=generate_password_hash(password),
        )

        try:
            db.session.add(new_user)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("That email address is already registered.", "danger")
            return redirect(url_for("register"))

        flash(
            "Account created successfully. Please log in.",
            "success",
        )
        return redirect(url_for("login"))

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
@limiter.limit(
    "5 per minute",
    methods=["POST"],
)
def login():
    if "user_id" in session:
        if session.get("role") == "admin":
            return redirect(url_for("admin"))

        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(
            user.password,
            password,
        ):
            flash("Invalid email or password.", "danger")
            return redirect(url_for("login"))

        session.clear()
        session["user_id"] = user.id
        session["user_name"] = user.full_name
        session["role"] = user.role

        flash("Login successful.", "success")

        if user.role == "admin":
            return redirect(url_for("admin"))

        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/auctions")
def auctions():
    close_all_expired_auctions()

    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip().lower()
    sort = request.args.get("sort", "latest").strip().lower()

    if status not in {"", "active", "closed"}:
        status = ""

    if sort not in {"latest", "low", "high", "ending"}:
        sort = "latest"

    query = Auction.query

    if search:
        query = query.filter(
            Auction.title.ilike(f"%{search}%")
        )

    if status == "active":
        query = query.filter(Auction.status == "Active")
    elif status == "closed":
        query = query.filter(Auction.status == "Closed")

    if sort == "low":
        query = query.order_by(Auction.current_price.asc())
    elif sort == "high":
        query = query.order_by(Auction.current_price.desc())
    elif sort == "ending":
        query = query.order_by(Auction.end_time.asc())
    else:
        query = query.order_by(Auction.created_at.desc())

    return render_template(
        "auctions.html",
        auctions=query.all(),
        search=search,
        status=status,
        sort=sort,
    )


@app.route("/auction/<int:auction_id>")
def auction_details(auction_id):
    auction = Auction.query.get_or_404(auction_id)

    if close_expired_auction(auction):
        db.session.commit()

    bids = (
        Bid.query
        .filter_by(auction_id=auction.id)
        .order_by(
            Bid.bid_amount.desc(),
            Bid.bid_time.asc(),
        )
        .all()
    )

    winner = (
        bids[0]
        if auction.status == "Closed" and bids
        else None
    )

    return render_template(
        "auction_details.html",
        auction=auction,
        bids=bids,
        winner=winner,
    )


@app.route("/check-auction/<int:auction_id>")
def check_auction(auction_id):
    auction = Auction.query.get_or_404(auction_id)

    if close_expired_auction(auction):
        db.session.commit()

    return jsonify(status=auction.status)


@app.route("/auction-data/<int:auction_id>")
def auction_data(auction_id):
    auction = Auction.query.get_or_404(auction_id)

    if close_expired_auction(auction):
        db.session.commit()

    bids = (
        Bid.query
        .filter_by(auction_id=auction.id)
        .order_by(
            Bid.bid_amount.desc(),
            Bid.bid_time.asc(),
        )
        .all()
    )

    return jsonify(
        current_price=auction.current_price,
        status=auction.status,
        bids=[
            {
                "user": bid.user.full_name,
                "amount": bid.bid_amount,
                "time": utc_to_india_display(bid.bid_time),   
            }
            for bid in bids
        ],
    )


# =========================================================
# Logged-in user routes
# =========================================================

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        user_name=session.get("user_name"),
    )


@app.route("/my-bids")
@login_required
def my_bids():
    bids = (
        Bid.query
        .filter_by(user_id=session["user_id"])
        .order_by(Bid.bid_time.desc())
        .all()
    )

    return render_template(
        "my_bids.html",
        bids=bids,
    )


@app.route(
    "/place-bid/<int:auction_id>",
    methods=["POST"],
)
@limiter.limit("10 per minute")
@login_required
def place_bid(auction_id):
    if session.get("role") == "admin":
        flash(
            "Administrators are not allowed to place bids.",
            "danger",
        )
        return redirect(
            url_for("auction_details", auction_id=auction_id)
        )

    bid_amount = parse_positive_amount(
        request.form.get("bid_amount", ""),
        "Bid amount",
    )

    if bid_amount is None:
        return redirect(
            url_for("auction_details", auction_id=auction_id)
        )

    try:
        auction = db.session.execute(
            db.select(Auction)
            .where(Auction.id == auction_id)
            .with_for_update()
        ).scalar_one_or_none()

        if auction is None:
            abort(404)

        now = utc_now()

        if now < auction.start_time:
            db.session.rollback()
            flash(
                "This auction has not started yet.",
                "danger",
            )
            return redirect(
                url_for(
                    "auction_details",
                    auction_id=auction.id,
                )
            )

        if (
            auction.status == "Closed"
            or now >= auction.end_time
        ):
            auction.status = "Closed"
            db.session.commit()

            flash(
                "This auction has already ended.",
                "danger",
            )
            return redirect(
                url_for(
                    "auction_details",
                    auction_id=auction.id,
                )
            )

        current_price = Decimal(str(auction.current_price))

        if bid_amount <= current_price:
            db.session.rollback()
            flash(
                "Your bid must be higher than the current price.",
                "danger",
            )
            return redirect(
                url_for(
                    "auction_details",
                    auction_id=auction.id,
                )
            )

        db.session.add(
            Bid(
                bid_amount=float(bid_amount),
                user_id=session["user_id"],
                auction_id=auction.id,
            )
        )

        auction.current_price = float(bid_amount)
        db.session.commit()

    except HTTPException:
        db.session.rollback()
        raise

    except Exception:
        db.session.rollback()
        app.logger.exception(
            "Bid placement failed for auction %s",
            auction_id,
        )

        flash(
            "The bid could not be placed. Please try again.",
            "danger",
        )
        return redirect(
            url_for("auction_details", auction_id=auction_id)
        )

    flash("Bid placed successfully.", "success")

    return redirect(
        url_for("auction_details", auction_id=auction_id)
    )


# =========================================================
# Administrator routes
# =========================================================

@app.route("/admin")
@admin_required
def admin():
    close_all_expired_auctions()

    recent_bids = (
        Bid.query
        .order_by(Bid.bid_time.desc())
        .limit(5)
        .all()
    )

    recent_auctions = (
        Auction.query
        .order_by(Auction.created_at.desc())
        .limit(5)
        .all()
    )

    return render_template(
        "admin_dashboard.html",
        total_users=User.query.count(),
        total_auctions=Auction.query.count(),
        active_auctions=Auction.query.filter_by(
            status="Active"
        ).count(),
        closed_auctions=Auction.query.filter_by(
            status="Closed"
        ).count(),
        total_bids=Bid.query.count(),
        recent_bids=recent_bids,
        recent_auctions=recent_auctions,
    )


@app.route(
    "/add-auction",
    methods=["GET", "POST"],
)
@admin_required
def add_auction():
    if request.method == "POST":
        form_data = parse_auction_form()

        if form_data is None:
            return redirect(url_for("add_auction"))

        image = request.files.get("image")
        uploaded_public_id = None

        try:
            if image and image.filename:
                image_url, uploaded_public_id = (
                    save_uploaded_image(image)
                )
            else:
                image_url = (
                    form_data["image_url"] or None
                )

            new_auction = Auction(
                title=form_data["title"],
                description=form_data["description"],

                # Cloudinary images are saved using image_url.
                # image remains None so existing templates work.
                image=None,
                image_url=image_url,

                starting_price=float(
                    form_data["starting_price"]
                ),
                current_price=float(
                    form_data["starting_price"]
                ),
                start_time=form_data["start_time"],
                end_time=form_data["end_time"],
                status="Active",
            )

            db.session.add(new_auction)
            db.session.commit()

        except ValueError as error:
            db.session.rollback()

            # Remove the Cloudinary image if database saving failed.
            delete_cloudinary_image(
                uploaded_public_id
            )

            flash(str(error), "danger")
            return redirect(url_for("add_auction"))

        except Exception:
            db.session.rollback()

            delete_cloudinary_image(
                uploaded_public_id
            )

            app.logger.exception(
                "Auction creation failed"
            )

            flash(
                "The auction could not be created.",
                "danger",
            )
            return redirect(url_for("add_auction"))

        flash(
            "Auction added successfully.",
            "success",
        )
        return redirect(url_for("admin"))

    return render_template("add_auction.html")


@app.route(
    "/edit-auction/<int:auction_id>",
    methods=["GET", "POST"],
)
@admin_required
def edit_auction(auction_id):
    auction = Auction.query.get_or_404(auction_id)

    if request.method == "POST":
        form_data = parse_auction_form(
            require_future_end=False,
        )

        if form_data is None:
            return redirect(
                url_for(
                    "edit_auction",
                    auction_id=auction.id,
                )
            )

        has_bids = (
            Bid.query
            .filter_by(auction_id=auction.id)
            .first()
            is not None
        )

        new_starting_price = form_data["starting_price"]

        if (
            has_bids
            and new_starting_price
            >= Decimal(str(auction.current_price))
        ):
            flash(
                "Starting price must remain below the "
                "current bid after bidding has begun.",
                "danger",
            )
            return redirect(
                url_for(
                    "edit_auction",
                    auction_id=auction.id,
                )
            )

        image = request.files.get("image")

        uploaded_public_id = None
        image_replaced = False

        # Save details of the previous image.
        old_local_filename = auction.image
        old_image_url = auction.image_url

        try:
            # Admin uploaded a new image.
            if image and image.filename:
                new_image_url, uploaded_public_id = (
                    save_uploaded_image(image)
                )

                auction.image = None
                auction.image_url = new_image_url
                image_replaced = True

            # Admin entered a different external image URL.
            elif form_data["image_url"]:
                new_external_url = form_data["image_url"]

                if (
                    auction.image is not None
                    or new_external_url != auction.image_url
                ):
                    auction.image = None
                    auction.image_url = new_external_url
                    image_replaced = True

            auction.title = form_data["title"]
            auction.description = form_data["description"]

            auction.starting_price = float(
                new_starting_price
            )

            auction.start_time = form_data["start_time"]
            auction.end_time = form_data["end_time"]

            if not has_bids:
                auction.current_price = float(
                    new_starting_price
                )

            now_utc = datetime.now(
               timezone.utc
            ).replace(tzinfo=None)

            if now_utc >= auction.end_time:
                auction.status = "Closed"
            else:
                auction.status = "Active"

            db.session.commit()

        except ValueError as error:
            db.session.rollback()

            # Remove the newly uploaded image when updating fails.
            delete_cloudinary_image(
                uploaded_public_id
            )

            flash(str(error), "danger")

            return redirect(
                url_for(
                    "edit_auction",
                    auction_id=auction.id,
                )
            )

        except Exception:
            db.session.rollback()

            delete_cloudinary_image(
                uploaded_public_id
            )

            app.logger.exception(
                "Auction update failed for auction %s",
                auction_id,
            )

            flash(
                "The auction could not be updated.",
                "danger",
            )

            return redirect(
                url_for(
                    "edit_auction",
                    auction_id=auction.id,
                )
            )

        # Delete the previous image only after the database update succeeds.
        if image_replaced:
            # Delete an old legacy local image.
            delete_uploaded_image(
                old_local_filename
            )

            # Delete an old Cloudinary image.
            old_public_id = get_cloudinary_public_id(
                old_image_url
            )

            delete_cloudinary_image(
                old_public_id
            )

        flash(
            "Auction updated successfully.",
            "success",
        )

        return redirect(url_for("auctions"))

    return render_template(
        "edit_auction.html",
        auction=auction,
    )

@app.route(
    "/delete-auction/<int:auction_id>",
    methods=["POST"],
)
@admin_required
def delete_auction(auction_id):
    auction = Auction.query.get_or_404(auction_id)

    # Save image information before deleting the database row.
    old_local_filename = auction.image
    old_image_url = auction.image_url

    # Get Cloudinary public ID.
    cloudinary_public_id = get_cloudinary_public_id(
        old_image_url
    )

    try:
        db.session.delete(auction)
        db.session.commit()

    except Exception:
        db.session.rollback()

        app.logger.exception(
            "Auction deletion failed for auction %s",
            auction_id,
        )

        flash(
            "The auction could not be deleted.",
            "danger",
        )

        return redirect(url_for("auctions"))

    # Delete an old image from static/uploads, if present.
    delete_uploaded_image(
        old_local_filename
    )

    # Delete the Cloudinary image, if present.
    delete_cloudinary_image(
        cloudinary_public_id
    )

    flash(
        "Auction deleted successfully.",
        "success",
    )

    return redirect(url_for("auctions"))

@app.template_filter("india_datetime_local")
def india_datetime_local_filter(value):
    return utc_to_india_datetime_local(value)


# =========================================================
# Error handlers
# =========================================================

@app.errorhandler(404)
def page_not_found(error):
    return render_template("404.html"), 404


@app.errorhandler(413)
def file_too_large(error):
    flash(
        "The selected image is too large. Maximum size is 5 MB.",
        "danger",
    )
    return redirect(
        request.referrer or url_for("admin")
    )

@app.errorhandler(429)
def too_many_requests(error):
    return render_template("429.html"), 429

@app.errorhandler(500)
def internal_server_error(error):
    db.session.rollback()
    return render_template("500.html"), 500


if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    app.run(
        debug=True,
        port=5001,
    )
