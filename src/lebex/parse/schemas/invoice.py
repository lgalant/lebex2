from decimal import Decimal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import HttpUrl
from pydantic import field_validator


def parse_decimal(v: object) -> object:
    if isinstance(v, str):
        # Remove thousands separators (commas) before parsing
        v = v.replace(",", "")
    return v


class Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class InvoiceRequest(Base):
    url: HttpUrl


class Entity(Base):
    tin: None | str = Field(
        None,
        description="Tax Identification Number, is a unique identifier "
        "assigned to a business or individual for tax purposes.",
    )
    name: None | str = Field(
        None,
        description="The full, formal name as it appears in "
        "the national registry of legal entities.",
    )
    address: None | str = Field(
        None,
        description="The physical address of the entity.",
    )
    zip_code: None | str = Field(
        None,
        description="A five-digit code that represents the geographical area "
        "where the entity is located.",
    )
    city: None | str = Field(
        None,
        description="The city where the entity is located.",
    )
    state: None | str = Field(
        None, description="The province or state where the entity is located."
    )
    tax_status: None | str = Field(
        None,
        description="Refers to the way in which taxes are treated.",
    )


class Seller(Entity):
    """The person or organization that creates an invoice"""


class Buyer(Entity):
    """The person or organization that receives an invoice"""


class Tax(Base):
    """A compulsory contribution to state revenue, levied by the government
    on business profits, or added to the cost of some goods, services,
    and transactions."""

    name: None | str = Field(
        None,
        description="Name of the tax",
    )
    rate: None | Decimal = Field(
        None,
        description="Rate of the tax",
    )
    amount: None | Decimal = Field(
        None,
        description="Amount of the tax",
    )

    @field_validator("rate", "amount", mode="before")
    @classmethod
    def clean_decimal(cls, v: object) -> object:
        return parse_decimal(v)


class Item(Base):
    """An individual item or service listed on an invoice"""

    code: None | str = Field(
        None,
        description="A unique identifier assigned.",
    )
    unit_of_measurement: None | str = Field(
        None,
        description="The unit used to measure.",
    )
    quantity: None | Decimal = Field(
        None,
        description="The number of units or the amount.",
    )
    description: None | str = Field(
        None,
        description="A detailed explanation.",
    )
    taxes: None | list[Tax] = Field(
        None,
        description="The taxes applied.",
    )
    unit_price: None | Decimal = Field(
        None,
        description="The cost of a single unit.",
    )
    discounts: None | Decimal = Field(
        None,
        description="A reduction in the price.",
    )
    amount: None | Decimal = Field(
        None,
        description="The total cost.",
    )

    @field_validator("quantity", "unit_price", "discounts", "amount", mode="before")
    @classmethod
    def clean_decimal(cls, v: object) -> object:
        return parse_decimal(v)


class Invoice(Base):
    seller: None | Seller = None
    buyer: None | Buyer = None

    payment_method: None | str = Field(
        None,
        description="Payment Method of the operation.",
    )
    currency: None | str = Field(
        None,
        description="Currency of the operation.",
    )
    number: None | str = Field(
        None,
        description="A unique identifier for each invoice that helps the "
        "issuer and recipient track and manage the invoice",
    )
    point_of_sale: None | str = Field(
        None,
        description="The place at which goods are retailed",
    )
    issue_date: None | str = Field(
        None,
        description="The date when an invoice is issued and the "
        "transaction is officially recorded",
    )
    expiration_date: None | str = Field(
        None,
        description="The deadline by which the invoice must be paid.",
    )
    exchange_rate: None | Decimal = Field(
        None,
        description="The price of one currency in relation to another",
    )
    total_amount: None | Decimal = Field(
        None,
        description="The total amount the customer is expected to pay, "
        "including the cost of the product or service, taxes, discounts, "
        "and any delivery or shipping charges",
    )
    subtotal: None | Decimal = Field(
        None,
        description="The total cost of all items listed on an invoice before "
        "any taxes, discounts, or shipping fees are applied.",
    )
    net_amount: None | Decimal = Field(
        None,
        description="The total cost of the goods or services provided, "
        "before any taxes or fees are added.",
    )
    taxes: None | list[Tax] = Field(
        None,
        description="The taxes applied.",
    )
    discounts: None | Decimal = Field(
        None,
        description="A reduction in the price.",
    )
    exempt_amount: None | Decimal = Field(
        None,
        description="The portion of a sale that is not subject to sales tax.",
    )
    withheld_amount: None | Decimal = Field(
        None,
        description="A portion of the total invoice amount that is deducted "
        "and held back by the payer.",
    )
    authorization_code: None | str = Field(
        None,
        description="A unique code generated by a payment processor or "
        "financial institution",
    )
    invoice_type: None | str = Field(
        None,
        description="Tax classification of the invoice as defined "
        "by local tax authorities",
    )
    items: None | list[Item] = Field(
        None,
        description="The individual products or services that are being "
        "billed, along with their quantities, rates, and prices",
    )

    @field_validator(
        "exchange_rate",
        "total_amount",
        "subtotal",
        "net_amount",
        "discounts",
        "exempt_amount",
        "withheld_amount",
        mode="before",
    )
    @classmethod
    def clean_decimal(cls, v: object) -> object:
        return parse_decimal(v)
