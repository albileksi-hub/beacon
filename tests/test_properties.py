"""Properties that must hold for inputs nobody thought to write down.

The example-based tests here were written by someone imagining what an
attacker might send. These are the same claims put to a generator that does
not share those assumptions, which is the point: every interesting bug found
in this project came from an input its author had not pictured.
"""

from decimal import ROUND_HALF_UP, Decimal

from hypothesis import assume, given
from hypothesis import strategies as st

from app.schemas import EventIn
from app.services.accounts import normalise_domain
from app.services.urls import belongs_to, host_of, pathname_of

# Hostname labels: the characters a real domain may use.
labels = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=12).filter(
    lambda s: not s.startswith("-") and not s.endswith("-")
)
domains = st.builds(lambda a, b: f"{a}.{b}", labels, labels)


prefixes = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=8)


@given(domain=domains, prefix=prefixes)
def test_a_host_that_merely_ends_with_the_domain_is_never_accepted(domain, prefix):
    """The suffix trap, against strings nobody chose.

    `notexample.com` ends with `example.com`. The leading dot in the check is
    what stops that, and this is the claim stated as a rule rather than as the
    two examples someone happened to think of: no matter what is glued to the
    front, it is a different site unless a dot separates them.
    """
    assume(not prefix.endswith("-"))
    hostile = f"https://{prefix}{domain}/anything"

    assert belongs_to(hostile, domain) is False, hostile


@given(domain=domains, suffix=labels)
def test_the_domain_as_a_prefix_of_someone_elses_is_never_accepted(domain, suffix):
    """`example.com.evil.test` contains the domain and belongs to an attacker."""
    hostile = f"https://{domain}.{suffix}.test/"

    assert belongs_to(hostile, domain) is False, hostile


@given(domain=domains, sub=labels, path=st.text(max_size=20))
def test_a_subdomain_is_accepted_however_the_rest_of_the_url_looks(domain, sub, path):
    """The other direction: the check must not reject traffic it exists to keep.

    Whatever the path, query or fragment, the host is what decides.
    """
    assert belongs_to(f"https://{sub}.{domain}/{path}", domain) is True


@given(
    host=domains,
    path=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789/-_", min_size=1, max_size=30),
    query=st.text(min_size=1, max_size=40),
)
def test_a_query_string_never_survives_into_a_pathname(host, path, query):
    """The privacy promise, as a rule rather than as the ?email= example.

    Query strings routinely carry tokens, session ids and email addresses. The
    claim is not "the cases we listed are stripped" but "nothing after the
    question mark is ever kept".
    """
    assume("#" not in query and "?" not in query)
    # Stated as "the query changes nothing", not "the query does not appear in
    # the result". Hypothesis rejected the second immediately with query="0",
    # which is a single character that turns up inside paths all the time --
    # a true property of the code, wrongly written.
    with_query = pathname_of(f"https://{host}/{path}?{query}")
    without = pathname_of(f"https://{host}/{path}")

    assert with_query == without
    assert "?" not in with_query


@given(domain=domains)
def test_normalising_a_domain_is_stable(domain):
    """Normalising twice must equal normalising once.

    A collector that normalised differently on the second pass would accept an
    event and then fail to find the site it was for.
    """
    once = normalise_domain(domain)

    assert normalise_domain(once) == once


@given(
    amount=st.decimals(
        min_value=Decimal("0"), max_value=Decimal("1000000"), places=2, allow_nan=False
    )
)
def test_money_is_never_lost_in_conversion(amount):
    """Minor units must be the amount a till would print, for every amount.

    The float bug that prompted this was found with one example, 0.29. This is
    the same claim over the whole range: parse it, convert it, and the pennies
    must be exactly the ones that went in.
    """
    payload = EventIn(
        site_id="blue-mug.example",
        url="https://blue-mug.example/x",
        revenue=amount,
    )
    expected = int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))

    assert payload.revenue_minor == expected


@given(url=st.text(max_size=60))
def test_host_of_never_raises_whatever_it_is_handed(url):
    """It runs on the collector's path, on a value a stranger chose."""
    result = host_of(url)

    assert result is None or isinstance(result, str)
