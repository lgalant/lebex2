from lebex.tools.invoice import WRITE_TOOLS as INVOICE_WRITE_TOOLS
from lebex.tools.purchase import LOOKUP_TOOLS as PURCHASE_LOOKUP_TOOLS
from lebex.tools.purchase import WRITE_TOOLS as PURCHASE_WRITE_TOOLS
from lebex.tools.queries import READ_TOOLS

ALL_TOOLS = READ_TOOLS + PURCHASE_LOOKUP_TOOLS + PURCHASE_WRITE_TOOLS + INVOICE_WRITE_TOOLS
