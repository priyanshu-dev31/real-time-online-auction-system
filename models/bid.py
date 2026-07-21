from extension import db
from datetime import datetime

class Bid(db.Model):

    __tablename__ = "bids"

    id = db.Column(db.Integer, primary_key=True)

    bid_amount = db.Column(db.Float, nullable=False)

    bid_time = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False
    )

    auction_id = db.Column(
        db.Integer,
        db.ForeignKey("auctions.id"),
        nullable=False
    )