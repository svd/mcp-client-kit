"""RFC 6570 URI template parsing and expansion (scalar values, levels 1-4)."""

from typing import Any

import pytest

from mcpgen import codegen
from mcpgen.codegen import UriTemplateError, UriVar

# RFC 6570 section 3.2 example variables, scalar members only (lists and
# associative arrays are out of scope). `undef` is absent and `bar` is None:
# both are undefined.
_RFC_VARS: dict[str, Any] = {
    "dub": "me/too",
    "hello": "Hello World!",
    "half": "50%",
    "var": "value",
    "who": "fred",
    "base": "http://example.com/home/",
    "path": "/foo/bar",
    "v": "6",
    "x": "1024",
    "y": "768",
    "empty": "",
    "bar": None,
}

# (template, expansion) pairs from RFC 6570 sections 1.2 and 3.2.2-3.2.9.
_RFC_TABLE = [
    # Level 1 / 3.2.2 simple string expansion
    ("{var}", "value"),
    ("{hello}", "Hello%20World%21"),
    ("{half}", "50%25"),
    ("O{empty}X", "OX"),
    ("O{undef}X", "OX"),
    ("{x,y}", "1024,768"),
    ("{x,hello,y}", "1024,Hello%20World%21,768"),
    ("?{x,empty}", "?1024,"),
    ("?{x,undef}", "?1024"),
    ("?{undef,y}", "?768"),
    ("map?{x,y}", "map?1024,768"),
    ("{var:3}", "val"),
    ("{var:30}", "value"),
    # Level 2 / 3.2.3 reserved expansion
    ("{+var}", "value"),
    ("{+hello}", "Hello%20World!"),
    ("{+half}", "50%25"),
    ("{base}index", "http%3A%2F%2Fexample.com%2Fhome%2Findex"),
    ("{+base}index", "http://example.com/home/index"),
    ("O{+empty}X", "OX"),
    ("O{+undef}X", "OX"),
    ("{+path}/here", "/foo/bar/here"),
    ("here?ref={+path}", "here?ref=/foo/bar"),
    ("up{+path}{var}/here", "up/foo/barvalue/here"),
    ("{+x,hello,y}", "1024,Hello%20World!,768"),
    ("{+path,x}/here", "/foo/bar,1024/here"),
    ("{+path:6}/here", "/foo/b/here"),
    # Level 2 / 3.2.4 fragment expansion
    ("{#var}", "#value"),
    ("X{#var}", "X#value"),
    ("{#hello}", "#Hello%20World!"),
    ("X{#hello}", "X#Hello%20World!"),
    ("{#half}", "#50%25"),
    ("foo{#empty}", "foo#"),
    ("foo{#undef}", "foo"),
    ("{#x,hello,y}", "#1024,Hello%20World!,768"),
    ("{#path,x}/here", "#/foo/bar,1024/here"),
    ("{#path:6}/here", "#/foo/b/here"),
    # Level 3 / 3.2.5 label expansion
    ("{.who}", ".fred"),
    ("{.who,who}", ".fred.fred"),
    ("{.half,who}", ".50%25.fred"),
    ("X{.var}", "X.value"),
    ("X{.x,y}", "X.1024.768"),
    ("X{.empty}", "X."),
    ("X{.undef}", "X"),
    ("X{.var:3}", "X.val"),
    # Level 3 / 3.2.6 path segment expansion
    ("{/who}", "/fred"),
    ("{/who,who}", "/fred/fred"),
    ("{/half,who}", "/50%25/fred"),
    ("{/who,dub}", "/fred/me%2Ftoo"),
    ("{/var}", "/value"),
    ("{/var,empty}", "/value/"),
    ("{/var,undef}", "/value"),
    ("{/var,x}/here", "/value/1024/here"),
    ("{/var:1,var}", "/v/value"),
    # Level 3 / 3.2.7 path-style parameter expansion
    ("{;who}", ";who=fred"),
    ("{;half}", ";half=50%25"),
    ("{;empty}", ";empty"),
    ("{;v,empty,who}", ";v=6;empty;who=fred"),
    ("{;v,bar,who}", ";v=6;who=fred"),
    ("{;x,y}", ";x=1024;y=768"),
    ("{;x,y,empty}", ";x=1024;y=768;empty"),
    ("{;x,y,undef}", ";x=1024;y=768"),
    ("{;hello:5}", ";hello=Hello"),
    # Level 3 / 3.2.8 form-style query expansion
    ("{?who}", "?who=fred"),
    ("{?half}", "?half=50%25"),
    ("{?x,y}", "?x=1024&y=768"),
    ("{?x,y,empty}", "?x=1024&y=768&empty="),
    ("{?x,y,undef}", "?x=1024&y=768"),
    ("{?var:3}", "?var=val"),
    # Level 3 / 3.2.9 form-style query continuation
    ("{&who}", "&who=fred"),
    ("{&half}", "&half=50%25"),
    ("?fixed=yes{&x}", "?fixed=yes&x=1024"),
    ("{&x,y,empty}", "&x=1024&y=768&empty="),
    ("{&var:3}", "&var=val"),
    # Explode on a scalar has no effect (section 3.2.1).
    ("{var*}", "value"),
    ("{/who*}", "/fred"),
    ("{;x*}", ";x=1024"),
    ("{?x*,y}", "?x=1024&y=768"),
]

# Literal handling beyond the RFC table: characters outside unreserved/reserved
# are pct-encoded (section 3.1), existing pct-triplets are kept.
_LITERAL_TABLE = [
    ("dimension://by-unit-type/{var}", "dimension://by-unit-type/value"),
    ("a b/{var}", "a%20b/value"),
    ("%41{var}", "%41value"),
    ("100%/{var}", "100%25/value"),
    ("no-expressions", "no-expressions"),
    ("", ""),
]


@pytest.mark.parametrize(("template", "expected"), _RFC_TABLE + _LITERAL_TABLE)
def test_expand_rfc_examples(template, expected):
    assert codegen.expand_uri_template(template, _RFC_VARS) == expected


def test_none_is_undefined():
    tpl = "dimension://by-unit-type/{unitType}{?page,size}"
    assert codegen.expand_uri_template(tpl, {"unitType": "X", "page": None, "size": "2"}) == (
        "dimension://by-unit-type/X?size=2"
    )
    assert codegen.expand_uri_template(tpl, {"unitType": "X", "page": None}) == "dimension://by-unit-type/X"
    assert codegen.expand_uri_template("{a,b}", {"a": None, "b": None}) == ""
    assert codegen.expand_uri_template("{#a}", {}) == ""


def test_space_and_unicode_are_pct_encoded():
    assert codegen.expand_uri_template("x/{v}", {"v": "Delivery Unit"}) == "x/Delivery%20Unit"
    assert codegen.expand_uri_template("x/{v}", {"v": "é"}) == "x/%C3%A9"
    assert codegen.expand_uri_template("x/{+v}", {"v": "é/ü"}) == "x/%C3%A9/%C3%BC"


def test_reserved_operators_keep_reserved_characters():
    value = {"v": "a/b?c=d&e#f:g"}
    assert codegen.expand_uri_template("{v}", value) == "a%2Fb%3Fc%3Dd%26e%23f%3Ag"
    assert codegen.expand_uri_template("{+v}", value) == "a/b?c=d&e#f:g"
    assert codegen.expand_uri_template("{#v}", value) == "#a/b?c=d&e#f:g"
    # A pct-triplet survives reserved expansion; a bare % does not.
    assert codegen.expand_uri_template("{+v}", {"v": "%2F%zz"}) == "%2F%25zz"
    assert codegen.expand_uri_template("{v}", {"v": "%2F"}) == "%252F"


def test_non_string_scalars_are_stringified():
    assert codegen.expand_uri_template("{?n}", {"n": 5}) == "?n=5"


def test_bools_expand_lowercase():
    assert codegen.expand_uri_template("{?a,b}", {"a": True, "b": False}) == "?a=true&b=false"
    assert codegen.expand_uri_template("x/{v}", {"v": True}) == "x/true"


def test_prefix_counts_code_points_before_encoding():
    assert codegen.expand_uri_template("{v:1}", {"v": "éa"}) == "%C3%A9"


@pytest.mark.parametrize(
    "template",
    [
        "{var",
        "var}",
        "a}b{var}",
        "{}",
        "{a{b}}",
        "{?}",
        "{var,}",
        "{,var}",
        "{bad-name}",
        "{.var.}",
        "{var..x}",
        "{var:0}",
        "{var:01}",
        "{var:10000}",
        "{var:}",
        "{var*:3}",
        "{var:3*}",
        "{=var}",
        "{!var}",
        "{@var}",
        "{|var}",
        "{%zzvar}",
        "{ var}",
    ],
)
def test_malformed_templates_raise(template):
    with pytest.raises(UriTemplateError):
        codegen.parse_uri_template(template)
    with pytest.raises(UriTemplateError):
        codegen.expand_uri_template(template, {})
    with pytest.raises(UriTemplateError):
        codegen.uri_template_variables(template)


def test_uri_template_error_is_value_error():
    assert issubclass(UriTemplateError, ValueError)


def test_parse_structure():
    parts = codegen.parse_uri_template("a{/x:3,y*}b{?q}")
    assert parts == [
        "a",
        (UriVar("x", "/", 3, False), UriVar("y", "/", None, True)),
        "b",
        (UriVar("q", "?", None, False),),
    ]


def test_varnames_allow_dots_and_pct_triplets():
    assert codegen.uri_template_variables("{a.b,c%20d,_1}") == [("a.b", True), ("c%20d", True), ("_1", True)]
    assert codegen.expand_uri_template("{a.b}", {"a.b": "v"}) == "v"


def test_required_optional_classification():
    tpl = "x{a}{+b}{#c}{.d}{/e}{;f}{?g,h}{&i}"
    assert codegen.uri_template_variables(tpl) == [
        ("a", True),
        ("b", True),
        ("c", True),
        ("d", True),
        ("e", True),
        ("f", True),
        ("g", False),
        ("h", False),
        ("i", False),
    ]
    assert [v.required for v in codegen.parse_uri_template("{x}{?y}")[1]] == [False]


def test_variable_used_twice_is_listed_once_and_required_if_any_use_is():
    assert codegen.uri_template_variables("{/var:1,var}") == [("var", True)]
    assert codegen.uri_template_variables("{?q}/{q}") == [("q", True)]
    assert codegen.uri_template_variables("{?q}{&q}") == [("q", False)]
    assert codegen.uri_template_variables("static://uri") == []


def test_emitted_helper_runs_standalone():
    # Generated modules must not import mcpgen.codegen; the helper needs only
    # `Any` from the module header and imports `quote` itself.
    assert "mcpgen" not in codegen._EXPAND_URI
    assert codegen._EXPAND_URI.startswith("def _expand_uri(")
    ns: dict = {"Any": Any}
    exec(compile(codegen._EXPAND_URI, "_expand_uri", "exec"), ns)
    helper = ns["_expand_uri"]
    assert set(ns) == {"Any", "_expand_uri", "__builtins__"}
    assert helper("{+base}index{?x,undef}", _RFC_VARS) == "http://example.com/home/index?x=1024"
    assert helper("dimension://by-unit-type/{v}", {"v": "Delivery Unit"}) == "dimension://by-unit-type/Delivery%20Unit"
