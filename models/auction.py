from extension import db
from datetime import datetime

class Auction(db.Model):

    __tablename__ = "auctions"

    id = db.Column(db.Integer, primary_key=True)

    title = db.Column(db.String(200), nullable=False)

    description = db.Column(db.Text, nullable=False)

    image = db.Column(db.String(255), nullable=True)
    image_url = db.Column(db.String(500), nullable=True)

    starting_price = db.Column(db.Float, nullable=False)

    current_price = db.Column(db.Float, nullable=False)

    start_time = db.Column(db.DateTime, nullable=False)

    end_time = db.Column(db.DateTime, nullable=False)

    status = db.Column(db.String(20), default="Active")

    bids = db.relationship(
    "Bid",
    backref="auction",
    lazy=True,
    cascade="all, delete-orphan"
)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)