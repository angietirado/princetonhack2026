from typing import Generic, Literal, TypeVar
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal["books", "electronics", "furniture", "clothing", "sports", "other"]
Condition = Literal["new", "like_new", "good", "fair"]
Status = Literal["available", "reserved", "sold"]


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
    display_name: str = Field(min_length=2, max_length=60)


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

    _photo = field_validator("image_url")(valid_photo)


class ListingPatch(Input):
    title: str | None = Field(default=None, min_length=3, max_length=100)
    description: str | None = Field(default=None, min_length=10, max_length=4000)
    price_cents: int | None = Field(default=None, ge=0, le=100_000_000, strict=True)
    category: Category | None = None
    condition: Condition | None = None
    pickup_location: str | None = Field(default=None, min_length=2, max_length=160)
    image_url: str | None = Field(default=None, max_length=2048)
    status: Status | None = None

    _photo = field_validator("image_url")(valid_photo)

    @field_validator("title", "description", "price_cents", "category", "condition", "pickup_location", "status")
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


class ConversationListing(BaseModel):
    id: str
    title: str
    price_cents: int
    image_url: str | None
    status: Literal["available", "reserved", "sold", "removed"]


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
