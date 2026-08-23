"""Formatting helpers shared by the web pages, the Word template and the PDF."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal("0.01")

_ONES = [
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen",
]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def trim_decimal(value) -> str:
    """
    Drop pointless trailing zeros without ever using exponent notation.

    Decimal("10.00").normalize() is Decimal("1E+1"), which would print as
    "1E+1% off" on a bill - hence the explicit ``:f`` formatting.
    """
    if value in (None, ""):
        return "0"
    dec = Decimal(str(value))
    trimmed = dec.normalize()
    if trimmed == trimmed.to_integral_value():
        trimmed = trimmed.quantize(Decimal("1"))
    return f"{trimmed:f}"


def format_inr(value, *, symbol: str = "", decimals: int = 2) -> str:
    """
    Indian digit grouping: 1234567.5 -> '12,34,567.50'.

    The last group has three digits, every group before it has two.
    """
    if value in (None, ""):
        value = 0
    amount = Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    negative = amount < 0
    amount = abs(amount)

    whole, _, frac = f"{amount:.{decimals}f}".partition(".")

    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pairs.insert(0, head)
        grouped = ",".join(pairs + [tail])
    else:
        grouped = whole

    out = f"{grouped}.{frac}" if decimals else grouped
    if symbol:
        out = f"{symbol} {out}"
    return f"-{out}" if negative else out


def _two_digits_to_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f" {_ONES[ones]}" if ones else "")


def _three_digits_to_words(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} Hundred")
    if rest:
        parts.append(_two_digits_to_words(rest))
    return " ".join(parts)


def amount_in_words(value, *, currency: str = "Rupees", subunit: str = "Paise") -> str:
    """
    Indian-system amount in words, as printed on invoices.

    1234567.50 -> 'Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty
    Seven and Fifty Paise Only'
    """
    amount = Decimal(str(value or 0)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    negative = amount < 0
    amount = abs(amount)

    rupees = int(amount)
    paise = int((amount - rupees) * 100)

    if rupees == 0:
        words = "Zero"
    else:
        crore, remainder = divmod(rupees, 10_000_000)
        lakh, remainder = divmod(remainder, 100_000)
        thousand, remainder = divmod(remainder, 1_000)

        chunks = []
        if crore:
            chunks.append(f"{_three_digits_to_words(crore) if crore > 99 else _two_digits_to_words(crore)} Crore")
        if lakh:
            chunks.append(f"{_two_digits_to_words(lakh)} Lakh")
        if thousand:
            chunks.append(f"{_two_digits_to_words(thousand)} Thousand")
        if remainder:
            chunks.append(_three_digits_to_words(remainder))
        words = " ".join(c for c in chunks if c)

    out = f"{currency} {words}"
    if paise:
        out += f" and {_two_digits_to_words(paise)} {subunit}"
    out += " Only"
    return f"Minus {out}" if negative else out
