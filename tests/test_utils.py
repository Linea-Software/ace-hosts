"""Unit tests for the shared helpers in scripts/utils.py."""

from pathlib import Path

from scripts import utils


def test_extract_domains_classic_hosts_lines():
    assert utils.extract_domains("0.0.0.0 example.com") == ["example.com"]
    assert utils.extract_domains("127.0.0.1 example.org") == ["example.org"]
    assert utils.extract_domains("::1 ipv6.example.net") == ["ipv6.example.net"]
    assert utils.extract_domains("0.0.0.0 a.com b.com c.com") == ["a.com", "b.com", "c.com"]
    assert utils.extract_domains("0.0.0.0 example.com # inline comment") == ["example.com"]


def test_extract_domains_bare_domains():
    assert utils.extract_domains("example.com") == ["example.com"]
    assert utils.extract_domains("EXAMPLE.COM.") == ["example.com"]
    assert utils.extract_domains("  spaced.example.com  ") == ["spaced.example.com"]


def test_extract_domains_adblock_syntax():
    assert utils.extract_domains("||ads.example.com^") == ["ads.example.com"]
    assert utils.extract_domains("||ads.example.com^$third-party") == ["ads.example.com"]
    assert utils.extract_domains("*.wild.example.com") == ["wild.example.com"]


def test_extract_domains_schemes_and_paths():
    assert utils.extract_domains("https://site.example.com/path") == ["site.example.com"]
    assert utils.extract_domains("http://www.example.com") == ["www.example.com"]


def test_extract_domains_ignores_junk():
    assert utils.extract_domains("") == []
    assert utils.extract_domains("   ") == []
    assert utils.extract_domains("# full line comment") == []
    assert utils.extract_domains("#") == []
    assert utils.extract_domains("0.0.0.0") == []
    assert utils.extract_domains("127.0.0.1") == []
    assert utils.extract_domains("not a domain!") == ["not", "a"]
    assert utils.extract_domains("0.0.0.0 -bad.example.com") == []
    assert utils.extract_domains("0.0.0.0 bad..example.com") == []
    assert utils.extract_domains("0.0.0.0 bad_example.com") == ["bad_example.com"]


def test_extract_domains_reserved_localhost_entries():
    assert utils.extract_domains("localhost") == []
    assert utils.extract_domains("127.0.0.1 localhost localhost.localdomain") == []
    assert utils.extract_domains("0.0.0.0 broadcasthost") == []


def test_extract_domains_idn_punycode():
    assert utils.extract_domains("0.0.0.0 xn--porn-hab.example") == ["xn--porn-hab.example"]


def test_is_valid_domain():
    assert utils.is_valid_domain("example.com")
    assert utils.is_valid_domain("sub.domain.example.co.uk")
    assert utils.is_valid_domain("xn--porn-hab.example")
    assert utils.is_valid_domain("singlelabel")
    assert not utils.is_valid_domain("-leading.example.com")
    assert not utils.is_valid_domain("trailing-.example.com")
    assert not utils.is_valid_domain("double..dot.example.com")
    assert not utils.is_valid_domain("spaces in.example.com")
    assert not utils.is_valid_domain("")


def test_iter_domains_handles_bom_and_crlf(tmp_path: Path):
    source = tmp_path / "with-bom.txt"
    # UTF-8 BOM + CRLF line endings, as found on some Windows-hosted lists.
    source.write_bytes(b"\xef\xbb\xbf0.0.0.0 alpha.example.com\r\n127.0.0.1 beta.example.org\r\n")
    assert list(utils.iter_domains(source)) == ["alpha.example.com", "beta.example.org"]


def test_sanitize_source_filename():
    assert utils.sanitize_source_filename("https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts") == "hosts"
    assert utils.sanitize_source_filename("https://example.com/a?b=1") == "a_b_1"
    assert utils.sanitize_source_filename("https://example.com/") == "source"


def test_human_size():
    assert utils.human_size(0) == "0 B"
    assert utils.human_size(1023) == "1023 B"
    assert utils.human_size(1024) == "1.0 KiB"
    assert utils.human_size(90 * 1024 * 1024) == "90.0 MiB"


def test_config_bool():
    assert utils.config_bool("true")
    assert utils.config_bool("1")
    assert utils.config_bool("YES")
    assert not utils.config_bool("false")
    assert not utils.config_bool("0")
    assert not utils.config_bool("")


def test_hosts_header_and_chunk_header():
    header = utils.hosts_header(42, ["one", "two"])
    assert header[0].startswith("#")
    assert any("Entries: 42" in line for line in header)
    assert any("Sources: one, two" in line for line in header)
    assert utils.chunk_header(3) == ["# ace-hosts blocklist - chunk 03"]
