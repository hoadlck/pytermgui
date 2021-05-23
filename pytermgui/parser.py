"""
pytermgui.parser
----------------
author: bczsalba


This module provides a tokenizer for both ANSI and markup type strings,
and some higher level methods for converting between the two.


The tags available are:
    - /                                         (reset: 0)
    - bold                                      (1)
    - dim                                       (2)
    - italic                                    (3)
    - underline                                 (4)
    - blink                                     (5)
    - blink2                                    (6)
    - inverse                                   (7)
    - invisible                                 (8)
    - strikethrough                             (9)

    - removers of all of the above              ([ /{tag} ])

    - black                                     color code 0
    - red                                       color code 1
    - green                                     color code 2
    - yellow                                    color code 3
    - blue                                      color code 4
    - magenta                                   color code 5
    - cyan                                      color code 6
    - white                                     color code 7

    - 4-bit colors                              (0-16)
    - 8-bit colors                              (16-256)
    - 24-bit colors                             (RGB: [rrr;bbb;ggg], HEX: [#rr;bb;gg])
    - /fg                                       unset foreground color (go to default)
    - /bg                                       unset background color (go to default)

    - background versions of all of the above   ([ @{color} ])

Future:
    - !save                                     save current color attributes
    - !restore                                  restore saved color attributes


Notes on syntax:
    - The tokenizer only yields tokens if it is different from the previous one,
        so if your markup is `[bold bold bold]hello` it will only yield 1 bold token.

    - The tokenizer also implicitly adds a reset tag at the end of all strings it's given,
        if it doesn't already end in one.


Example syntax:
    >>> from pytermgui import markup_to_ansi, ansi_to_markup

    >>> ansi = markup_to_ansi("[@141 60 bold italic]Hello[/italic underline inverse]There!")
    '\\x1b[48;5;141m\\x1b[38;5;60m\\x1b[1m\\x1b[3mHello\\x1b[23m\\x1b[4m\\x1b[7mThere!'\\x1b[0m'

    >>> markup = ansi_to_markup(ansi)
    '[@141 60 bold italic]Hello[/italic underline inverse]There![/]''
"""

import re
from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional, Iterator

from .ansi_interface import foreground, reset, bold


__all__ = [
    "escape_ansi",
    "tokenize_ansi",
    "tokenize_markup",
    "markup_to_ansi",
    "ansi_to_markup",
    "prettify_markup",
]

RE_ANSI = re.compile(r"(?:\x1b\[(.*?)m)|(?:\x1b\](.*?)\x1b\\)")
RE_TAGS = re.compile(r"""((\\*)\[([a-z0-9!#@\/].*?)\])""", re.VERBOSE)

NAMES = [
    "/",
    "bold",
    "dim",
    "italic",
    "underline",
    "blink",
    "blink2",
    "inverse",
    "invisible",
    "strikethrough",
]

UNSET_MAP = {
    "/bold": "22",
    "/dim": "22",
    "/italic": "23",
    "/underline": "24",
    "/blink": "25",
    "/blink2": "26",
    "/inverse": "27",
    "/invisible": "28",
    "/strikethrough": "29",
    "/fg": "39",
    "/bg": "49",
}


def escape_ansi(text: str) -> str:
    """Escape ANSI sequence"""

    return text.encode("unicode_escape").decode("utf-8")


def _handle_color(tag: str) -> Optional[tuple[str, bool]]:
    """Handle color fore/background, hex conversions"""

    # sort out non-colors
    if not all(char.isdigit() or char in "#;@abcdef" for char in tag):
        return None

    # convert into background color
    if tag.startswith("@"):
        pretext = "48;"
        tag = tag[1:]
    else:
        pretext = "38;"

    hexcolor = tag.startswith("#")

    # 8-bit (256) and 24-bit (RGB) colors use different id numbers
    color_pre = "5;" if tag.count(";") < 2 and not hexcolor else "2;"

    # hex colors are essentially RGB in a different format
    if hexcolor:
        tag = ";".join(str(val) for val in foreground.translate_hex(tag))

    return pretext + color_pre + tag, pretext == "48;"


def _handle_named_color(tag: str) -> Optional[tuple[str, int]]:
    """Check if name is in foreground.names and return its value if possible"""

    if not tag.lstrip("@") in foreground.names:
        return None

    if tag.startswith("@"):
        tag = tag[1:]
        background = "@"

    else:
        background = ""

    return _handle_color(background + str(foreground.names[tag]))


class TokenAttribute(Enum):
    """Special attribute for a Token"""

    CLEAR = auto()
    COLOR = auto()
    STYLE = auto()
    BACKGROUND_COLOR = auto()


@dataclass
class Token:
    """Result of ansi tokenized string."""

    start: int
    end: int
    plain: Optional[str] = None
    code: Optional[str] = None
    attribute: Optional[TokenAttribute] = None

    def to_name(self) -> str:
        """Convert token to named attribute for rich text"""

        if self.plain is not None:
            return self.plain

        # only one of [self.code, self.plain] can be None
        assert self.code is not None

        if self.code is not None and self.code.isdigit():
            if self.code in UNSET_MAP.values():
                for key, value in UNSET_MAP.items():
                    if value == self.code:
                        return key

            index = int(self.code)
            return NAMES[index]

        out = ""
        numbers = self.code.split(";")

        if numbers[0] == "48":
            out += "@"

        if len(numbers) < 3:
            raise SyntaxError(
                f'Invalid ANSI code "{escape_ansi(self.code)}" in token {self}.'
            )

        simple = int(numbers[2])
        if simple in foreground.names.values():
            for name, fore_value in foreground.names.items():
                if fore_value == simple:
                    return out + name

        out += ";".join(numbers[2:])

        return out

    def to_sequence(self) -> str:
        """Convert token to an ANSI sequence"""

        if self.code is None:
            # only one of [self.code, self.plain] can be None
            assert self.plain is not None
            return self.plain

        return "\x1b[" + self.code + "m"


def tokenize_ansi(text: str) -> Iterator[Token]:
    """Tokenize text containing ANSI sequences
    Todo: add attributes to ansi tokens"""

    position = start = end = 0
    previous = None

    if not text.endswith(reset()):
        text += "\x1b[0m"

    for match in RE_ANSI.finditer(text):
        start, end = match.span(0)
        sgr, _ = match.groups()

        # add plain text between last and current sequence
        if start > position:
            token = Token(start, end, plain=text[position:start])
            previous = token
            yield token

        token = Token(start, end, code=sgr)

        if previous is None or not previous.code == sgr:
            previous = token
            yield token

        position = end

    if position < len(text):
        yield Token(start, end, plain=text[position:])


def tokenize_markup(text: str) -> Iterator[Token]:
    """Tokenize markup text"""

    position = 0
    for match in RE_TAGS.finditer(text):
        _, escapes, tag_text = match.groups()
        start, end = match.span()

        if start > position:
            yield Token(start, end, plain=text[position:start])

        if escapes is not None:
            backslashes, escaped = divmod(len(escapes), 2)
            if backslashes > 0:
                yield Token(start, end, plain=backslashes * "\\")
                start += backslashes * 2

            if escaped > 0:
                yield Token(start, end, plain=tag_text[len(escapes) :])
                position = end
                continue

        for tag in tag_text.split():
            if tag in NAMES:
                yield Token(
                    start,
                    end,
                    code=str(NAMES.index(tag)),
                    attribute=(
                        TokenAttribute.CLEAR if tag == "/" else TokenAttribute.STYLE
                    ),
                )

            elif tag in UNSET_MAP:
                yield Token(
                    start,
                    end,
                    code=str(UNSET_MAP[tag]),
                    attribute=TokenAttribute.CLEAR,
                )

            else:
                if (data := _handle_named_color(tag)) is not None:
                    code, background = data
                    yield Token(
                        start,
                        end,
                        code=code,
                        attribute=(
                            TokenAttribute.BACKGROUND_COLOR
                            if background
                            else TokenAttribute.COLOR
                        ),
                    )

                elif (data := _handle_color(tag)) is not None:
                    color, background = data
                    yield Token(
                        start,
                        end,
                        code=color,
                        attribute=(
                            TokenAttribute.BACKGROUND_COLOR
                            if background
                            else TokenAttribute.COLOR
                        ),
                    )

                else:
                    raise SyntaxError(f'Markup tag "{tag}" is not recognized.')

        position = end

    if position < len(text):
        yield Token(start, end, plain=text[position:])


def markup_to_ansi(markup: str) -> str:
    """Turn markup text into ANSI str"""

    ansi = ""
    for token in tokenize_markup(markup):
        ansi += token.to_sequence()

    if not ansi.endswith(reset()):
        ansi += reset()

    return ansi


def ansi_to_markup(ansi: str) -> str:
    """Turn ansi text into markup"""

    markup = ""
    in_attr = False
    current_bracket: list[str] = []

    for token in tokenize_ansi(ansi):
        # start/add to attr bracket
        if token.code is not None:
            in_attr = True
            current_bracket.append(token.to_name())
            continue

        # close previous attr bracket
        if in_attr:
            markup += "[" + " ".join(current_bracket) + "]"
            current_bracket = []

        markup += token.to_name()

    markup += "[" + " ".join(current_bracket) + "]"
    return markup


def prettify_markup(markup: str) -> str:
    """Return syntax-highlighted markup"""

    def _style_attributes(attributes: list[Token]) -> str:
        """Style all attributes"""

        styled = bold("[")
        for i, item in enumerate(attributes):
            if i > 0:
                styled += " "

            if item.attribute is TokenAttribute.CLEAR:
                styled += foreground(item.to_name(), 210)
                continue

            if item.attribute is TokenAttribute.COLOR:
                styled += item.to_sequence() + item.to_name() + reset()
                continue

            if item.attribute is TokenAttribute.BACKGROUND_COLOR:
                seq = item.to_sequence()
                numbers = seq.split(";")
                numbers[0] = bold("", reset_style=False) + "\x1b[38"
                styled += ";".join(numbers) + item.to_name() + reset()
                continue

            if (seq := item.to_sequence()) :
                styled += seq

            styled += foreground(item.to_name(), 114)

        return styled + bold("]")

    visual_bracket: list[Token] = []
    applied_bracket: list[str] = []

    out = ""
    for token in tokenize_markup(markup):
        if token.code:
            if token.attribute is TokenAttribute.CLEAR:
                name = token.to_name()
                if name == "/":
                    applied_bracket = []
                else:
                    code = token.code
                    if code == "22":
                        offset = 21
                    else:
                        offset = 20

                    sequence = "\x1b[" + str(int(code) - offset) + "m"
                    if sequence in applied_bracket:
                        applied_bracket.remove(
                                sequence
                        )

            applied_bracket.append(token.to_sequence())
            visual_bracket.append(token)
            continue

        if len(visual_bracket) > 0:
            out += _style_attributes(visual_bracket)
            visual_bracket = []

        out += "".join(applied_bracket) + token.to_name() + reset()

    if len(visual_bracket) > 0:
        out += _style_attributes(visual_bracket)

    return out

    # "[italic bold 141;35;60]hello there[/][141] alma[/] [18;218;168 underline]fa"
