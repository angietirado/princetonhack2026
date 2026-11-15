from typing import Generic, Literal, TypeVar
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Category = Literal["books", "electronics", "furniture", "clothing", "sports", "other"]
Condition = Literal["new", "like_new", "good", "fair"]
Status = Literal["available", "reserved", "sold"]
ListingKind = Literal["sale", "rent", "free", "urgent", "wanted"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmailInput(Input):
    email: str = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def student_email(cls, value: str) -> str:
        try:
            address = validate_email(value, check_deliverability=False, allow_smtputf8=False).normalized.lower()
        except EmailNotValidError as exc:
            raise ValueError("Enter a valid student email address") from exc
        domain = address.rsplit("@", 1)[1]
        if not domain.endswith(".edu") or len(domain) <= 4:
            raise ValueError("Use an email address ending in .edu")
        return address


class StudentInput(EmailInput):
    display_name: str | None = Field(default=None, min_length=2, max_length=60)


class DevSessionInput(Input):
    username: Literal["dev_student1", "dev_student2"]


class ProfileInput(Input):
    display_name: str | None = Field(default=None, min_length=2, max_length=60)
    campus_location: str | None = Field(default=None, min_length=2, max_length=160)

    @field_validator("display_name", "campus_location")
    @classmethod
    def not_null(cls, value):
        if value is None:
            raise ValueError("This field cannot be null")
        return value


def valid_photo(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Photo URL must be a public HTTPS URL without credentials")
        if any(char.isspace() for char in value):
            raise ValueError("Photo URL must not contain whitespace")
    except (ValueError, TypeError) as exc:
        raise ValueError("Photo URL must be a valid HTTPS URL") from exc
    return value


class ListingInput(Input):
    title: str = Field(min_length=3, max_length=100)
    description: str = Field(min_length=10, max_length=4000)
    price_cents: int = Field(ge=0, le=100_000_000, strict=True)
    category: Category
    condition: Condition
    pickup_location: str = Field(min_length=2, max_length=160)
    image_url: str | None = Field(default=None, max_length=2048)
    kind: ListingKind = "sale"
    needs_repair: bool = False

    _photo = field_validator("image_url")(valid_photo)

    @model_validator(mode="after")
    def valid_kind_price(self):
        if self.kind in {"free", "urgent", "wanted"} and self.price_cents != 0:
            raise ValueError("Free items and requests must have a zero price")
        if self.kind == "rent" and self.price_cents == 0:
            raise ValueError("Rentals must have a positive daily price")
        return self


class ListingPatch(Input):
    title: str | None = Field(default=None, min_length=3, max_length=100)
    description: str | None = Field(default=None, min_length=10, max_length=4000)
    price_cents: int | None = Field(default=None, ge=0, le=100_000_000, strict=True)
    category: Category | None = None
    condition: Condition | None = None
    pickup_location: str | None = Field(default=None, min_length=2, max_length=160)
    image_url: str | None = Field(default=None, max_length=2048)
    status: Status | None = None
    kind: ListingKind | None = None
    needs_repair: bool | None = None

    _photo = field_validator("image_url")(valid_photo)

    @field_validator("title", "description", "price_cents", "category", "condition", "pickup_location", "status", "kind", "needs_repair")
    @classmethod
    def not_null(cls, value):
        if value is None:
            raise ValueError("This field cannot be null")
        return value


class ConversationInput(Input):
    message: str = Field(min_length=1, max_length=2000)


class MessageInput(Input):
    body: str = Field(min_length=1, max_length=2000)


class PublicUser(BaseModel):
    id: str
    display_name: str
    university: str
    edu_eligible: Literal[True]
    email_verified: Literal[False]


class User(PublicUser):
    email: str
    created_at: str
    dev_username: str | None = None
    campus_location: str = "Campus Center"
    iou_tickets: int = 0


class DevStudent(BaseModel):
    username: str
    id: str
    display_name: str
    email: str


class DevStudents(BaseModel):
    items: list[DevStudent]


class Listing(BaseModel):
    id: str
    title: str
    description: str
    price_cents: int
    category: Category
    condition: Condition
    pickup_location: str
    image_url: str | None
    status: Status
    seller: PublicUser
    created_at: str
    updated_at: str
    is_demo: bool
    kind: ListingKind = "sale"
    needs_repair: bool = False
    rental_period: Literal["day"] | None = None


class ConversationListing(BaseModel):
    id: str
    title: str
    price_cents: int
    image_url: str | None
    status: Literal["available", "reserved", "sold", "removed"]
    kind: ListingKind = "sale"
    pickup_location: str = "Campus Center"


class Conversation(BaseModel):
    id: str
    listing: ConversationListing
    buyer: PublicUser
    seller: PublicUser
    last_message: str
    created_at: str
    updated_at: str


class Message(BaseModel):
    id: str
    conversation_id: str
    sender_id: str
    body: str
    created_at: str


Item = TypeVar("Item")


class Page(BaseModel, Generic[Item]):
    items: list[Item]
    total: int
    page: int
    page_size: int


class Health(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]


class AppConfig(BaseModel):
    email_policy: Literal["edu"]
    verification_mode: Literal["domain_only"]
    demo_data: bool
    devmode: bool


OrderStatus = Literal["requested", "accepted", "ready", "completed", "returned", "cancelled"]


class OrderInput(Input):
    message: str = Field(default="I would like to arrange pickup.", min_length=1, max_length=2000)
    pickup_location: str | None = Field(default=None, min_length=2, max_length=160)


class OrderPatch(Input):
    status: OrderStatus | None = None
    pickup_location: str | None = Field(default=None, min_length=2, max_length=160)

    @field_validator("status", "pickup_location")
    @classmethod
    def not_null(cls, value):
        if value is None:
            raise ValueError("This field cannot be null")
        return value


class Order(BaseModel):
    id: str
    listing: Listing
    buyer: PublicUser
    seller: PublicUser
    status: OrderStatus
    pickup_location: str
    conversation_id: str
    created_at: str
    updated_at: str


class RepairerInput(Input):
    specialties: list[str] = Field(min_length=1, max_length=8)
    about: str = Field(min_length=10, max_length=2000)
    rate_cents: int = Field(ge=0, le=100_000_000, strict=True)
    portfolio_image_url: str | None = Field(default=None, max_length=2048)

    _photo = field_validator("portfolio_image_url")(valid_photo)

    @field_validator("specialties")
    @classmethod
    def clean_specialties(cls, value):
        cleaned = list(dict.fromkeys(item.strip() for item in value))
        if any(len(item) < 2 or len(item) > 60 for item in cleaned):
            raise ValueError("Each specialty must be between 2 and 60 characters")
        return cleaned


class Repairer(BaseModel):
    id: str
    user: PublicUser
    specialties: list[str]
    about: str
    rate_cents: int
    portfolio_image_url: str | None
    verification: Literal["self_reported"] = "self_reported"
    created_at: str
    updated_at: str


RepairRequestStatus = Literal["requested", "accepted", "completed", "cancelled"]


class RepairRequestInput(Input):
    description: str = Field(min_length=10, max_length=2000)
    location: str = Field(min_length=2, max_length=160)


class RepairRequestPatch(Input):
    status: RepairRequestStatus


class RepairRequest(BaseModel):
    id: str
    repairer: Repairer
    requester: PublicUser
    description: str
    location: str
    status: RepairRequestStatus
    conversation_id: str
    created_at: str
    updated_at: str
