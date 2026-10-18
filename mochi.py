#!/usr/bin/env python3
"""
Mochi - Cute & Beautiful Desktop Screen Companion for Arch Linux (GNOME/Wayland).
Captures up to 5 screen pages or interactive snips and answers questions with a multimodal vision LLM.
Features:
1. Multi-Page Support: Attach up to 5 pages / snips to compare or analyze together.
2. Top-Island Floating Window (like Coucou): Anchored cleanly at the top of your screen.
3. Production-Grade Window Controls: Draggable island header, Minimize button, Esc to close.
4. Rich Markdown & Syntax-highlighted code blocks
5. Region Snip Mode (Super+Shift+C or --snip) & Full Screen (Super+C)
6. Multi-Turn Chat History with context memory & instant clean copy
"""

import sys
import os
import json
import time
import base64
import io
import re
import random
import threading
import subprocess
import urllib.request
import urllib.error
import urllib.parse
import math
import cairo
import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)

# Ensure direct bypass for proxies
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Prefer X11 backend when available to allow top-island window positioning (like Coucou)
if 'GDK_BACKEND' not in os.environ and os.environ.get('DISPLAY'):
    os.environ['GDK_BACKEND'] = 'x11,wayland'

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Xdp', '1.0')
try:
    gi.require_version('GdkX11', '4.0')
    from gi.repository import GdkX11
except Exception:
    pass
from gi.repository import Gtk, Adw, GLib, Gdk, Pango, Xdp

from PIL import Image

APP_DIR = os.path.dirname(os.path.realpath(__file__))
CONFIG_FILE = os.path.join(APP_DIR, 'config.json')
STYLES_FILE = os.path.join(APP_DIR, 'styles.css')
LOG_FILE = '/tmp/mochi.log'

def log(msg):
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

DEFAULT_CONFIG = {
    "api_base": "https://openrouter.ai/api/v1",
    "api_key": "",
    "models": [
        "google/gemini-2.5-flash-lite",
        "nvidia/nemotron-3.5-super-vl:free"
    ],
    "system_prompt": "You are Lexi, a sharp, delightful, and concise screen companion on Arch Linux. The user captured one or more pages/screenshots of their work and asked a question. Analyze all provided pages carefully. Provide a clear, accurate, and direct answer. Use clean markdown formatting with bullet points and code blocks where helpful.",
    "max_image_dimension": 800,
    "image_quality": 65,
    "max_tokens": 1000,
    "request_timeout": 35
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                return {**DEFAULT_CONFIG, **cfg}
        except Exception as e:
            log(f"Warning loading config: {e}")
    return DEFAULT_CONFIG

SYNTAX_KEYWORDS = {
    'def', 'class', 'import', 'from', 'return', 'if', 'else', 'elif', 'for', 'while',
    'try', 'except', 'finally', 'with', 'as', 'lambda', 'yield', 'async', 'await',
    'function', 'const', 'let', 'var', 'fn', 'pub', 'struct', 'enum', 'impl',
    'sudo', 'pacman', 'systemctl', 'git', 'echo', 'cat', 'grep', 'curl', 'python'
}

def create_markdown_tag_table():
    table = Gtk.TextTagTable()
    tags = [
        ('h1', {'foreground': '#ff758c', 'weight': Pango.Weight.BOLD, 'scale': 1.3}),
        ('h2', {'foreground': '#c77dff', 'weight': Pango.Weight.BOLD, 'scale': 1.15}),
        ('h3', {'foreground': '#7aa2f7', 'weight': Pango.Weight.BOLD, 'scale': 1.05}),
        ('bold', {'weight': Pango.Weight.BOLD}),
        ('italic', {'style': Pango.Style.ITALIC}),
        ('bold_italic', {'weight': Pango.Weight.BOLD, 'style': Pango.Style.ITALIC}),
        ('code_inline', {'family': 'monospace', 'background': '#232530', 'foreground': '#e5c07b'}),
        ('code_block', {'family': 'monospace', 'background': '#181825', 'foreground': '#cdd6f4', 'left-margin': 16, 'right-margin': 16}),
        ('code_keyword', {'family': 'monospace', 'foreground': '#cba6f7', 'weight': Pango.Weight.BOLD}),
        ('code_string', {'family': 'monospace', 'foreground': '#a6e3a1'}),
        ('code_comment', {'family': 'monospace', 'foreground': '#6c7086', 'style': Pango.Style.ITALIC}),
        ('code_number', {'family': 'monospace', 'foreground': '#fab387'}),
        ('code_header', {'family': 'monospace', 'foreground': '#9399b2', 'scale': 0.82, 'background': '#11111b', 'left-margin': 16, 'right-margin': 16}),
        ('bullet_symbol', {'foreground': '#ff758c', 'weight': Pango.Weight.BOLD}),
        ('bullet_text', {'left-margin': 14}),
        ('user_header', {'foreground': '#ff758c', 'weight': Pango.Weight.BOLD, 'scale': 1.05}),
        ('lexi_header', {'foreground': '#c084fc', 'weight': Pango.Weight.BOLD, 'scale': 1.05}),
        ('mochi_header', {'foreground': '#c084fc', 'weight': Pango.Weight.BOLD, 'scale': 1.05}),
        ('divider', {'foreground': '#45475a'}),
        ('quote', {'style': Pango.Style.ITALIC, 'foreground': '#a6adc8', 'left-margin': 18})
    ]
    for name, props in tags:
        tag = Gtk.TextTag.new(name)
        for k, v in props.items():
            tag.set_property(k, v)
        table.add(tag)
    return table

def parse_inline(text):
    pattern = re.compile(r'(`[^`]+`|\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|\*[^*]+\*|__[^_]+__|(?<!\w)_[^_]+_(?!\w))')
    pos = 0
    tokens = []
    for m in pattern.finditer(text):
        start, end = m.span()
        if start > pos:
            tokens.append(('plain', text[pos:start]))
        match_str = m.group(0)
        if match_str.startswith('`') and match_str.endswith('`'):
            tokens.append(('code_inline', match_str[1:-1]))
        elif match_str.startswith('***') and match_str.endswith('***'):
            tokens.append(('bold_italic', match_str[3:-3]))
        elif (match_str.startswith('**') and match_str.endswith('**')) or (match_str.startswith('__') and match_str.endswith('__')):
            tokens.append(('bold', match_str[2:-2]))
        elif (match_str.startswith('*') and match_str.endswith('*')) or (match_str.startswith('_') and match_str.endswith('_')):
            tokens.append(('italic', match_str[1:-1]))
        pos = end
    if pos < len(text):
        tokens.append(('plain', text[pos:]))
    return tokens

def highlight_code_line(line):
    comment_idx = -1
    for c_marker in ('#', '//'):
        idx = line.find(c_marker)
        if idx != -1:
            if comment_idx == -1 or idx < comment_idx:
                comment_idx = idx
    if comment_idx != -1:
        before_comment = line[:comment_idx]
        comment_text = line[comment_idx:]
    else:
        before_comment = line
        comment_text = None

    tokens = []
    pattern = re.compile(r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\b[A-Za-z_]\w*\b|\b\d+\b)')
    pos = 0
    for m in pattern.finditer(before_comment):
        start, end = m.span()
        if start > pos:
            tokens.append(('code_block', before_comment[pos:start]))
        word = m.group(0)
        if (word.startswith('"') and word.endswith('"')) or (word.startswith("'") and word.endswith("'")):
            tokens.append(('code_string', word))
        elif word in SYNTAX_KEYWORDS:
            tokens.append(('code_keyword', word))
        elif word.isdigit():
            tokens.append(('code_number', word))
        else:
            tokens.append(('code_block', word))
        pos = end
    if pos < len(before_comment):
        tokens.append(('code_block', before_comment[pos:]))

    if comment_text:
        tokens.append(('code_comment', comment_text))

    return tokens

def render_markdown_to_buffer(buffer, text):
    lines = text.split('\n')
    in_code_block = False
    code_lang = ""
    code_lines = []

    for line in lines:
        if line.strip().startswith('```'):
            if not in_code_block:
                in_code_block = True
                code_lang = line.strip()[3:].strip()
                code_lines = []
            else:
                in_code_block = False
                if code_lang:
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"  [{code_lang}]\n", 'code_header')
                for cline in code_lines:
                    hl_tokens = highlight_code_line(cline)
                    for tag_name, text_chunk in hl_tokens:
                        if tag_name == 'code_block':
                            buffer.insert_with_tags_by_name(buffer.get_end_iter(), text_chunk, 'code_block')
                        else:
                            buffer.insert_with_tags_by_name(buffer.get_end_iter(), text_chunk, 'code_block', tag_name)
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), '\n', 'code_block')
                buffer.insert(buffer.get_end_iter(), '\n')
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        stripped = line.strip()
        if stripped.startswith('### '):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"{stripped[4:]}\n", 'h3')
        elif stripped.startswith('## '):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"{stripped[3:]}\n", 'h2')
        elif stripped.startswith('# '):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"{stripped[2:]}\n", 'h1')
        elif stripped.startswith('> '):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"{stripped[2:]}\n", 'quote')
        elif stripped.startswith('- ') or stripped.startswith('* ') or stripped.startswith('+ '):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), "  • ", 'bullet_symbol')
            for tag_type, chunk in parse_inline(stripped[2:]):
                if tag_type == 'plain':
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), chunk, 'bullet_text')
                else:
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), chunk, tag_type, 'bullet_text')
            buffer.insert(buffer.get_end_iter(), '\n')
        elif re.match(r'^\d+\.\s', stripped):
            m = re.match(r'^(\d+\.\s)(.*)', stripped)
            prefix, content = m.group(1), m.group(2)
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"  {prefix}", 'bold')
            for tag_type, chunk in parse_inline(content):
                if tag_type == 'plain':
                    buffer.insert(buffer.get_end_iter(), chunk)
                else:
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), chunk, tag_type)
            buffer.insert(buffer.get_end_iter(), '\n')
        elif stripped in ('---', '***'):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), "────────────────────────────────────────\n", 'divider')
        elif stripped == '':
            buffer.insert(buffer.get_end_iter(), '\n')
        else:
            for tag_type, chunk in parse_inline(line):
                if tag_type == 'plain':
                    buffer.insert(buffer.get_end_iter(), chunk)
                else:
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), chunk, tag_type)
            buffer.insert(buffer.get_end_iter(), '\n')


class LexiMascotWidget(Gtk.DrawingArea):
    """
    Handcrafted Celestial Screen Spirit (Lexi) - Full-body Anime Chibi Kitten/Fox
    with expressive anime eyes, perky ears, working hands/paws, sitting feet with
    pink toe beans, swaying fluffy tail, dynamic mouse gaze tracking, and active working animations.
    """
    def __init__(self, width=115, height=130):
        super().__init__()
        self.mascot_w = width
        self.mascot_h = height
        self.set_content_width(width)
        self.set_content_height(height)
        self.add_css_class('lexi-mascot-area')
        self.set_draw_func(self.on_draw)

        # Gaze & Eye position (-1.0 to 1.0)
        self.look_x = 0.0
        self.look_y = 0.0
        self.target_look_x = 0.0
        self.target_look_y = 0.0

        # Animation states
        self.mood = "normal"  # "normal", "working", "happy", "thinking"
        self.is_blinking = False
        self.squish_x = 1.0
        self.squish_y = 1.0
        self.ear_wiggle = 0.0
        self.target_ear_wiggle = 0.0
        self.think_angle = 0.0
        self.paw_anim = 0.0
        self.tail_phase = 0.0

        # Direct click gesture to pet Lexi
        click = Gtk.GestureClick()
        click.connect('pressed', lambda *_: self.pet())
        self.add_controller(click)

        # Periodic timers for blinking and frame updates
        self.blink_timer = GLib.timeout_add(3800, self.trigger_blink)
        self.anim_timer = GLib.timeout_add(30, self.on_anim_frame)

    def trigger_blink(self):
        if self.mood == "normal" and not self.is_blinking:
            self.is_blinking = True
            self.queue_draw()
            GLib.timeout_add(160, self.finish_blink)
        return GLib.SOURCE_CONTINUE

    def finish_blink(self):
        self.is_blinking = False
        self.queue_draw()
        return GLib.SOURCE_REMOVE

    def set_gaze(self, lx, ly):
        self.target_look_x = max(-1.0, min(1.0, lx))
        self.target_look_y = max(-1.0, min(1.0, ly))

    def on_anim_frame(self):
        diff_x = self.target_look_x - self.look_x
        diff_y = self.target_look_y - self.look_y
        self.look_x += diff_x * 0.24
        self.look_y += diff_y * 0.24

        self.squish_x += (1.0 - self.squish_x) * 0.16
        self.squish_y += (1.0 - self.squish_y) * 0.16
        self.ear_wiggle += (self.target_ear_wiggle - self.ear_wiggle) * 0.18

        self.tail_phase = (self.tail_phase + 0.08) % (2 * math.pi)

        if self.mood in ("working", "thinking"):
            self.paw_anim = (self.paw_anim + 0.16) % 1.0
            self.think_angle = (self.think_angle + 0.12) % (2 * math.pi)

        needs_redraw = (abs(diff_x) > 0.005 or abs(diff_y) > 0.005 or
                        abs(self.squish_x - 1.0) > 0.005 or abs(self.ear_wiggle) > 0.005 or
                        self.mood in ("working", "thinking"))
        if needs_redraw:
            self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def pet(self):
        self.squish_x = 1.15
        self.squish_y = 0.88
        self.target_ear_wiggle = 0.18
        self.mood = "happy"
        self.queue_draw()
        GLib.timeout_add(2200, self.reset_mood)

    def set_mood(self, mood):
        self.mood = mood
        if mood == "happy":
            self.squish_x = 1.14
            self.squish_y = 0.88
            self.target_ear_wiggle = 0.14
        else:
            self.target_ear_wiggle = 0.0
        self.queue_draw()

    def reset_mood(self):
        self.mood = "normal"
        self.target_ear_wiggle = 0.0
        self.queue_draw()
        return GLib.SOURCE_REMOVE

    def on_draw(self, area, cr, w, h):
        cx, cy = w / 2.0, h / 2.0
        s = min(w / 110.0, h / 130.0)

        cr.save()
        cr.translate(cx, cy + 2 * s)
        cr.scale(self.squish_x, self.squish_y)

        # 1. Ambient Drop Shadow on Floor
        cr.save()
        cr.scale(1.0, 0.28)
        pat_sh = cairo.RadialGradient(0, 46 * s, 2 * s, 0, 46 * s, 34 * s)
        pat_sh.add_color_stop_rgba(0, 0.08, 0.05, 0.22, 0.45)
        pat_sh.add_color_stop_rgba(1, 0, 0, 0, 0.0)
        cr.set_source(pat_sh)
        cr.arc(0, 46 * s, 34 * s, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

        # 2. Fluffy Fox/Kitten Tail (Curving up on right with breathing sway)
        tail_sway = math.sin(self.tail_phase) * 0.12
        cr.save()
        cr.translate(14 * s, 18 * s)
        cr.rotate(0.20 + tail_sway)
        cr.move_to(0, 0)
        cr.curve_to(14 * s, 4 * s, 24 * s, -10 * s, 18 * s, -22 * s)
        cr.curve_to(14 * s, -28 * s, 5 * s, -24 * s, 7 * s, -14 * s)
        cr.curve_to(8 * s, -6 * s, 3 * s, -2 * s, 0, 0)
        cr.close_path()

        pat_tail = cairo.LinearGradient(0, 4 * s, 18 * s, -28 * s)
        pat_tail.add_color_stop_rgb(0, 0.88, 0.90, 0.98)
        pat_tail.add_color_stop_rgb(0.65, 0.96, 0.97, 1.0)
        pat_tail.add_color_stop_rgb(1.0, 1.0, 1.0, 1.0)
        cr.set_source(pat_tail)
        cr.fill_preserve()
        cr.set_line_width(1.0 * s)
        cr.set_source_rgba(0.65, 0.70, 0.85, 0.45)
        cr.stroke()

        cr.move_to(13 * s, -20 * s)
        cr.curve_to(15 * s, -16 * s, 9 * s, -18 * s, 7 * s, -14 * s)
        cr.set_line_width(0.8 * s)
        cr.set_source_rgba(0.72, 0.76, 0.90, 0.6)
        cr.stroke()
        cr.restore()

        # 3. Chubby Torso / Body with soft white bib
        cr.save()
        cr.move_to(-14 * s, 8 * s)
        cr.curve_to(-18 * s, 20 * s, -14 * s, 32 * s, 0, 33 * s)
        cr.curve_to(14 * s, 32 * s, 18 * s, 20 * s, 14 * s, 8 * s)
        cr.curve_to(10 * s, 0 * s, -10 * s, 0 * s, -14 * s, 8 * s)
        cr.close_path()

        pat_body = cairo.RadialGradient(-3 * s, 15 * s, 2 * s, 0, 20 * s, 22 * s)
        pat_body.add_color_stop_rgb(0.0, 1.0, 1.0, 1.0)
        pat_body.add_color_stop_rgb(0.65, 0.95, 0.97, 1.0)
        pat_body.add_color_stop_rgb(1.0, 0.87, 0.90, 0.98)
        cr.set_source(pat_body)
        cr.fill_preserve()
        cr.set_line_width(1.1 * s)
        cr.set_source_rgba(0.65, 0.70, 0.85, 0.45)
        cr.stroke()

        cr.move_to(-7 * s, 7 * s)
        cr.curve_to(-9 * s, 16 * s, -5 * s, 24 * s, 0, 26 * s)
        cr.curve_to(5 * s, 24 * s, 9 * s, 16 * s, 7 * s, 7 * s)
        cr.curve_to(3 * s, 4 * s, -3 * s, 4 * s, -7 * s, 7 * s)
        cr.close_path()
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.75)
        cr.fill()
        cr.restore()

        # 4. Cute Stubby Legs & Feet with pink toe beans
        for sign in [-1, 1]:
            cr.save()
            lx = sign * 10.5 * s
            ly = 30.0 * s
            cr.translate(lx, ly)
            cr.scale(1.0, 0.85)
            cr.arc(0, 0, 5.2 * s, 0, 2 * math.pi)
            pat_foot = cairo.LinearGradient(0, -5 * s, 0, 5 * s)
            pat_foot.add_color_stop_rgb(0, 1.0, 1.0, 1.0)
            pat_foot.add_color_stop_rgb(1, 0.88, 0.91, 0.98)
            cr.set_source(pat_foot)
            cr.fill_preserve()
            cr.set_line_width(1.0 * s)
            cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
            cr.stroke()

            cr.save()
            cr.scale(1.0, 0.65)
            cr.arc(0, 1.6 * s, 2.4 * s, 0, 2 * math.pi)
            cr.set_source_rgba(1.0, 0.55, 0.72, 0.85)
            cr.fill()
            for dx in [-2.2 * s, 0.0, 2.2 * s]:
                cr.arc(dx, -1.5 * s, 1.0 * s, 0, 2 * math.pi)
                cr.fill()
            cr.restore()
            cr.restore()

        # 5. Fluffy Kitten/Fox Ears (Wide base, triangular, perky outward tilt)
        for sign in [-1, 1]:
            cr.save()
            ear_base_x = sign * 13.5 * s
            ear_base_y = -18.0 * s
            cr.translate(ear_base_x, ear_base_y)
            cr.rotate(sign * 0.38 + (sign * self.ear_wiggle))

            # Outer ear
            cr.move_to(-sign * 6.5 * s, 6 * s)
            cr.curve_to(-sign * 3 * s, -6 * s, sign * 2 * s, -18 * s, sign * 6.5 * s, -16 * s)
            cr.curve_to(sign * 11 * s, -12 * s, sign * 8.5 * s, 1 * s, sign * 5.0 * s, 7 * s)
            cr.close_path()

            pat_ear = cairo.LinearGradient(0, -18 * s, 0, 6 * s)
            pat_ear.add_color_stop_rgb(0, 0.96, 0.98, 1.0)
            pat_ear.add_color_stop_rgb(1, 0.88, 0.91, 0.98)
            cr.set_source(pat_ear)
            cr.fill_preserve()
            cr.set_line_width(1.1 * s)
            cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
            cr.stroke()

            # Inner ear warm rosy pink fluff
            cr.move_to(-sign * 4.0 * s, 4 * s)
            cr.curve_to(-sign * 1.5 * s, -4 * s, sign * 2 * s, -12 * s, sign * 5.5 * s, -11 * s)
            cr.curve_to(sign * 8.0 * s, -8 * s, sign * 6.5 * s, 0, sign * 3.5 * s, 5 * s)
            cr.close_path()
            pat_inner = cairo.LinearGradient(0, -12 * s, 0, 4 * s)
            pat_inner.add_color_stop_rgba(0, 1.0, 0.45, 0.65, 0.88)
            pat_inner.add_color_stop_rgba(1, 0.98, 0.70, 0.80, 0.45)
            cr.set_source(pat_inner)
            cr.fill()

            # Ear-tip tuft
            cr.move_to(sign * 1.0 * s, 1 * s)
            cr.curve_to(sign * 3.5 * s, -2 * s, sign * 5.0 * s, -4 * s, sign * 5.5 * s, 0)
            cr.set_line_width(0.8 * s)
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.9)
            cr.stroke()
            cr.restore()

        # 6. Head Contour (Chubby cheeks, soft kitten silhouette, NO FOREHEAD DOT)
        cr.save()
        cr.move_to(0, -25 * s)
        cr.curve_to(17 * s, -25 * s, 24 * s, -12 * s, 24 * s, 0 * s)
        cr.curve_to(24 * s, 12 * s, 15 * s, 17 * s, 0, 17 * s)
        cr.curve_to(-15 * s, 17 * s, -24 * s, 12 * s, -24 * s, 0 * s)
        cr.curve_to(-24 * s, -12 * s, -17 * s, -25 * s, 0, -25 * s)
        cr.close_path()

        pat_head = cairo.RadialGradient(-5 * s, -10 * s, 3 * s, 0, -3 * s, 28 * s)
        pat_head.add_color_stop_rgb(0.0, 1.0, 1.0, 1.0)
        pat_head.add_color_stop_rgb(0.70, 0.96, 0.98, 1.0)
        pat_head.add_color_stop_rgb(1.0, 0.86, 0.89, 0.98)
        cr.set_source(pat_head)
        cr.fill_preserve()
        cr.set_line_width(1.2 * s)
        cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
        cr.stroke()

        # Cheek fur tufts
        for sign in [-1, 1]:
            cr.move_to(sign * 23.5 * s, -1 * s)
            cr.curve_to(sign * 26.5 * s, 1 * s, sign * 26 * s, 4 * s, sign * 22 * s, 5 * s)
            cr.set_line_width(0.9 * s)
            cr.set_source_rgba(0.68, 0.72, 0.86, 0.6)
            cr.stroke()

        # Soft top sheen
        cr.save()
        cr.scale(0.85, 0.42)
        pat_gloss = cairo.LinearGradient(0, -55 * s, 0, -25 * s)
        pat_gloss.add_color_stop_rgba(0, 1.0, 1.0, 1.0, 0.65)
        pat_gloss.add_color_stop_rgba(1, 1.0, 1.0, 1.0, 0.0)
        cr.set_source(pat_gloss)
        cr.arc(0, -38 * s, 16 * s, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

        # 7. Soft Cheek Blush
        for sign in [-1, 1]:
            cr.save()
            ch_x = sign * 14 * s
            ch_y = 3.5 * s
            pat_bl = cairo.RadialGradient(ch_x, ch_y, 0, ch_x, ch_y, 6.0 * s)
            pat_bl.add_color_stop_rgba(0.0, 1.0, 0.40, 0.55, 0.55)
            pat_bl.add_color_stop_rgba(0.6, 1.0, 0.55, 0.68, 0.25)
            pat_bl.add_color_stop_rgba(1.0, 1.0, 0.65, 0.75, 0.0)
            cr.set_source(pat_bl)
            cr.arc(ch_x, ch_y, 6.0 * s, 0, 2 * math.pi)
            cr.fill()

            cr.arc(ch_x - sign * 1.5 * s, ch_y - 0.5 * s, 0.7 * s, 0, 2 * math.pi)
            cr.arc(ch_x + sign * 1.5 * s, ch_y + 0.5 * s, 0.5 * s, 0, 2 * math.pi)
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.8)
            cr.fill()
            cr.restore()

        # 8. Cute Button Nose
        cr.save()
        cr.move_to(-1.2 * s, -0.2 * s)
        cr.line_to(1.2 * s, -0.2 * s)
        cr.line_to(0, 1.2 * s)
        cr.close_path()
        cr.set_source_rgb(0.95, 0.45, 0.62)
        cr.fill()
        cr.restore()

        # 9. Cute Cat Mouth
        cr.save()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        if self.mood == "happy":
            cr.move_to(-4.0 * s, 3.2 * s)
            cr.curve_to(-4.0 * s, 9.0 * s, 4.0 * s, 9.0 * s, 4.0 * s, 3.2 * s)
            cr.close_path()
            cr.set_source_rgb(0.24, 0.08, 0.22)
            cr.fill_preserve()
            cr.set_line_width(1.1 * s)
            cr.set_source_rgb(0.18, 0.05, 0.18)
            cr.stroke()

            cr.save()
            cr.move_to(-4.0 * s, 3.2 * s)
            cr.curve_to(-4.0 * s, 9.0 * s, 4.0 * s, 9.0 * s, 4.0 * s, 3.2 * s)
            cr.clip()
            cr.arc(0, 8.5 * s, 3.0 * s, 0, 2 * math.pi)
            cr.set_source_rgb(1.0, 0.48, 0.65)
            cr.fill()
            cr.restore()
        else:
            cr.set_line_width(1.3 * s)
            cr.set_source_rgba(0.22, 0.16, 0.32, 0.88)
            cr.move_to(-3.5 * s, 3.8 * s)
            cr.curve_to(-2.4 * s, 5.8 * s, -0.5 * s, 5.8 * s, 0, 4.4 * s)
            cr.curve_to(0.5 * s, 5.8 * s, 2.4 * s, 5.8 * s, 3.5 * s, 3.8 * s)
            cr.stroke()
        cr.restore()

        # 10. Expressive Anime Eyes (NO FOREHEAD DOT!)
        eye_spacing = 9.2 * s
        eye_y = -5.5 * s

        for sign in [-1, 1]:
            ex = sign * eye_spacing
            ey = eye_y

            # Eyebrow
            cr.save()
            cr.set_line_width(1.3 * s)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_source_rgba(0.28, 0.22, 0.42, 0.78)
            brow_lift = -1.4 * s if self.mood == "happy" else 0
            brow_tilt = sign * 0.5 * s if self.mood in ("working", "thinking") else 0
            cr.move_to(ex - 3.8 * s, ey - 7.5 * s + brow_lift - brow_tilt)
            cr.curve_to(ex - 1.2 * s, ey - 9.0 * s + brow_lift, ex + 1.2 * s, ey - 9.0 * s + brow_lift, ex + 3.8 * s, ey - 7.5 * s + brow_lift + brow_tilt)
            cr.stroke()
            cr.restore()

            if self.is_blinking:
                cr.save()
                cr.set_line_width(2.0 * s)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_source_rgba(0.18, 0.12, 0.32, 0.95)
                cr.move_to(ex - 4.8 * s, ey + 0.5 * s)
                cr.curve_to(ex - 2 * s, ey + 3.8 * s, ex + 2 * s, ey + 3.8 * s, ex + 4.8 * s, ey + 0.5 * s)
                cr.stroke()
                cr.move_to(ex + sign * 4.2 * s, ey + 0.8 * s)
                cr.line_to(ex + sign * 5.8 * s, ey - 0.2 * s)
                cr.stroke()
                cr.restore()

            elif self.mood == "happy":
                cr.save()
                cr.set_line_width(2.4 * s)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_source_rgba(0.18, 0.12, 0.32, 0.95)
                cr.move_to(ex - 4.8 * s, ey + 2.0 * s)
                cr.curve_to(ex - 2 * s, ey - 3.0 * s, ex + 2 * s, ey - 3.0 * s, ex + 4.8 * s, ey + 2.0 * s)
                cr.stroke()
                cr.set_line_width(1.5 * s)
                cr.move_to(ex + sign * 4.2 * s, ey + 1.2 * s)
                cr.line_to(ex + sign * 6.0 * s, ey - 0.8 * s)
                cr.stroke()
                cr.restore()

            elif self.mood in ("working", "thinking"):
                # Inquisitive upward starlight glance
                cr.save()
                cr.translate(ex, ey)
                cr.scale(4.2 * s, 5.6 * s)
                cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.fill()
                cr.restore()

                cr.save()
                cr.translate(ex, ey - 1.2 * s)
                cr.scale(3.2 * s, 4.4 * s)
                pat_iris = cairo.LinearGradient(0, -1, 0, 1)
                pat_iris.add_color_stop_rgb(0.0, 0.12, 0.05, 0.28)
                pat_iris.add_color_stop_rgb(1.0, 0.65, 0.20, 0.75)
                cr.set_source(pat_iris)
                cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                cr.fill()
                cr.restore()

                cr.save()
                cr.translate(ex, ey - 1.2 * s)
                cr.rotate(self.think_angle)
                cr.set_line_width(1.4 * s)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.move_to(-2.4 * s, 0)
                cr.line_to(2.4 * s, 0)
                cr.move_to(0, -2.4 * s)
                cr.line_to(0, 2.4 * s)
                cr.stroke()
                cr.arc(0, 0, 0.7 * s, 0, 2 * math.pi)
                cr.fill()
                cr.restore()

                cr.save()
                cr.set_line_width(1.8 * s)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_source_rgba(0.18, 0.12, 0.32, 0.95)
                cr.move_to(ex - 4.5 * s, ey - 2.5 * s)
                cr.curve_to(ex - 1.8 * s, ey - 5.8 * s, ex + 1.8 * s, ey - 5.8 * s, ex + 4.5 * s, ey - 2.5 * s)
                cr.stroke()
                cr.restore()

            else:
                # 1. Clean Sclera Base
                cr.save()
                cr.translate(ex, ey)
                cr.scale(4.2 * s, 5.8 * s)
                cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.fill_preserve()
                cr.set_line_width(0.3)
                cr.set_source_rgba(0.7, 0.75, 0.85, 0.4)
                cr.stroke()
                cr.restore()

                # 2. Clip iris strictly inside sclera
                cr.save()
                cr.save()
                cr.translate(ex, ey)
                cr.scale(4.2 * s, 5.8 * s)
                cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                cr.restore()
                cr.clip()

                # Jewel Iris tracking cursor
                px = ex + self.look_x * 2.2 * s
                py = ey + self.look_y * 2.0 * s
                cr.save()
                cr.translate(px, py)
                cr.scale(3.2 * s, 4.5 * s)
                pat_iris = cairo.LinearGradient(0, -1, 0, 1)
                pat_iris.add_color_stop_rgb(0.0, 0.08, 0.04, 0.22)
                pat_iris.add_color_stop_rgb(0.55, 0.45, 0.18, 0.68)
                pat_iris.add_color_stop_rgb(1.0, 0.95, 0.32, 0.68)
                cr.set_source(pat_iris)
                cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                cr.fill()
                cr.restore()

                # Obsidian Pupil
                cr.save()
                cr.translate(px, py - 0.2 * s)
                cr.scale(1.7 * s, 2.5 * s)
                cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                cr.set_source_rgb(0.05, 0.02, 0.10)
                cr.fill()
                cr.restore()

                # Triple Specular Catchlights
                cr.save()
                cr.arc(px - 1.2 * s, py - 1.5 * s, 1.3 * s, 0, 2 * math.pi)
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.fill()

                cr.arc(px + 1.2 * s, py + 1.4 * s, 0.75 * s, 0, 2 * math.pi)
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.92)
                cr.fill()

                cr.arc(px - 0.4 * s, py + 1.8 * s, 0.5 * s, 0, 2 * math.pi)
                cr.set_source_rgba(1.0, 0.85, 1.0, 0.9)
                cr.fill()
                cr.restore()

                cr.restore() # End clip

                # 3. Upper Eyelid & Winged Eyelash Line
                cr.save()
                cr.set_line_width(2.0 * s)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_source_rgba(0.16, 0.10, 0.28, 0.98)
                cr.move_to(ex - 4.6 * s, ey - 2.5 * s)
                cr.curve_to(ex - 2.0 * s, ey - 6.2 * s, ex + 2.0 * s, ey - 6.2 * s, ex + 4.6 * s, ey - 2.5 * s)
                cr.stroke()
                cr.set_line_width(1.4 * s)
                cr.move_to(ex + sign * 4.2 * s, ey - 3.0 * s)
                cr.line_to(ex + sign * 6.0 * s, ey - 4.8 * s)
                cr.stroke()
                cr.restore()

        cr.restore() # End Head save

        # 11. Cute Working Hands & Paws
        paw_phase = math.sin(self.paw_anim * 2 * math.pi) if self.mood in ("working", "thinking") else 0.0
        if self.mood in ("working", "thinking"):
            cr.save()
            # Left paw holding/typing
            cr.translate(-7 * s + paw_phase * 2 * s, 18 * s - paw_phase * 3 * s)
            cr.scale(1.0, 0.9)
            cr.arc(0, 0, 4.2 * s, 0, 2 * math.pi)
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.fill_preserve()
            cr.set_line_width(0.9 * s)
            cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
            cr.stroke()
            cr.arc(0, 0.5 * s, 2.0 * s, 0, 2 * math.pi)
            cr.set_source_rgba(1.0, 0.55, 0.72, 0.8)
            cr.fill()
            cr.restore()

            # Right paw holding cosmic glowing stylus
            cr.save()
            cr.translate(7 * s - paw_phase * 2 * s, 17 * s + paw_phase * 3 * s)
            cr.save()
            cr.rotate(-0.35)
            pat_stylus = cairo.LinearGradient(0, -10 * s, 0, 8 * s)
            pat_stylus.add_color_stop_rgb(0, 0.75, 0.5, 1.0)
            pat_stylus.add_color_stop_rgb(1, 0.35, 0.2, 0.6)
            cr.set_source(pat_stylus)
            cr.rectangle(-1.2 * s, -10 * s, 2.4 * s, 16 * s)
            cr.fill()
            cr.arc(0, -10 * s, 2.4 * s, 0, 2 * math.pi)
            cr.set_source_rgba(1.0, 0.8, 1.0, 0.9)
            cr.fill()
            cr.restore()

            cr.scale(1.0, 0.9)
            cr.arc(0, 0, 4.2 * s, 0, 2 * math.pi)
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.fill_preserve()
            cr.set_line_width(0.9 * s)
            cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
            cr.stroke()
            cr.arc(0, 0.5 * s, 2.0 * s, 0, 2 * math.pi)
            cr.set_source_rgba(1.0, 0.55, 0.72, 0.8)
            cr.fill()
            cr.restore()

        elif self.mood == "happy":
            for sign in [-1, 1]:
                cr.save()
                cr.translate(sign * 18 * s, 6 * s)
                cr.scale(1.0, 0.9)
                cr.arc(0, 0, 4.4 * s, 0, 2 * math.pi)
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.fill_preserve()
                cr.set_line_width(0.9 * s)
                cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
                cr.stroke()
                cr.arc(0, 0, 2.2 * s, 0, 2 * math.pi)
                cr.set_source_rgba(1.0, 0.55, 0.72, 0.85)
                cr.fill()
                cr.restore()

        else:
            # Resting little paws cutely in front
            for sign in [-1, 1]:
                cr.save()
                cr.translate(sign * 10.5 * s, 20.0 * s)
                cr.scale(1.0, 0.92)
                cr.arc(0, 0, 4.5 * s, 0, 2 * math.pi)
                pat_paw = cairo.LinearGradient(0, -4 * s, 0, 4 * s)
                pat_paw.add_color_stop_rgb(0, 1.0, 1.0, 1.0)
                pat_paw.add_color_stop_rgb(1, 0.90, 0.92, 0.98)
                cr.set_source(pat_paw)
                cr.fill_preserve()
                cr.set_line_width(0.9 * s)
                cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
                cr.stroke()

                cr.arc(0, 0.8 * s, 2.0 * s, 0, 2 * math.pi)
                cr.set_source_rgba(1.0, 0.55, 0.72, 0.8)
                cr.fill()

                cr.set_line_width(0.7 * s)
                cr.set_source_rgba(0.7, 0.75, 0.88, 0.6)
                cr.move_to(-1.5 * s, -2.5 * s)
                cr.line_to(-1.5 * s, -0.5 * s)
                cr.move_to(1.5 * s, -2.5 * s)
                cr.line_to(1.5 * s, -0.5 * s)
                cr.stroke()
                cr.restore()

        cr.restore() # End overall translate


class LexiWindow(Adw.ApplicationWindow):
    def __init__(self, app, config, is_snip=False):
        super().__init__(application=app, title="Lexi Cosmic Screen Copilot")
        self.app = app
        self.config = config
        self.is_snip = is_snip
        self.captured_pages = []  # List of {"b64": str, "texture": Gdk.Texture, "is_snip": bool}
        self.max_pages = 5
        self.saved_pos = None
        self.is_streaming = False
        self.timer_source_id = None
        self.query_start_time = 0
        self.conversation = []
        self.last_answer = ""
        self.turn_start_mark = None

        self.set_default_size(920, 360)
        self.set_resizable(True)
        self.set_decorated(False)
        self.add_css_class('lexi-window')
        self.add_css_class('mochi-window')

        # Build UI layout
        self.build_ui()

        # Keyboard controllers
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_ctrl.connect('key-pressed', self.on_key_pressed)
        self.add_controller(key_ctrl)

        # Mouse pointer motion tracking for dynamic eye physics (capture across all child widgets)
        motion_ctrl = Gtk.EventControllerMotion()
        motion_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        motion_ctrl.connect('motion', self.on_pointer_motion)
        self.add_controller(motion_ctrl)

        # Connect close request to clean up app reference
        self.connect('close-request', self.on_close_request)

        # Screen-wide pointer polling (when outside window)
        GLib.timeout_add(40, self.poll_screen_mouse)

        # Automatically reveal child if unminimized or activated
        def on_active_notify(win, _):
            if win.is_active() and hasattr(win, 'revealer') and not win.revealer.get_reveal_child():
                win.revealer.set_reveal_child(True)
        self.connect('notify::is-active', on_active_notify)

    def on_close_request(self, _):
        if hasattr(self, 'app') and self.app:
            self.app.active_window = None
        return False

    LEXI_PET_REACTIONS = [
        ("✨", "✨ *stardust!* ( ⸝⸝•ᴗ•⸝⸝ ) Navigating your screen!"),
        ("🪐", "🪐 *orbital spin!* You're the brightest star in the cosmos!"),
        ("🌟", "🌟 ( ˶ˆ꒳ˆ˵ ) Cosmic focus activated! What are we exploring?"),
        ("🚀", "🚀 *blastoff!* Let's conquer this together!"),
        ("🛸", "🛸 ( •̀ ᴗ •́ )و Beaming down solutions from orbit!"),
        ("🌙", "🌙 (*˘︶˘*).｡.:*♡ Floating peacefully in starlight..."),
        ("💫", "💫 ( ⊙‿⊙ ) Gravity stabilized! All systems ready!"),
    ]
    MOCHI_PET_REACTIONS = LEXI_PET_REACTIONS

    def on_pointer_motion(self, ctrl, x, y):
        if hasattr(self, 'mascot') and self.mascot:
            dx = (x - 75.0) / 440.0
            dy = (y - 75.0) / 280.0
            self.mascot.set_gaze(dx, dy)

    def poll_screen_mouse(self):
        if not self.get_visible() or not hasattr(self, 'mascot') or not self.mascot:
            return GLib.SOURCE_CONTINUE
        try:
            out = subprocess.check_output(['xdotool', 'getmouselocation'], stderr=subprocess.DEVNULL).decode()
            m = re.search(r'x:(\d+)\s+y:(\d+)', out)
            if m:
                mx, my = int(m.group(1)), int(m.group(2))
                pos = self.get_current_pos() or getattr(self, 'window_pos', None) or (500, 36)
                wx, wy = pos
                dx = (mx - (wx + 75.0)) / 560.0
                dy = (my - (wy + 75.0)) / 420.0
                self.mascot.set_gaze(dx, dy)
        except Exception:
            pass
        return GLib.SOURCE_CONTINUE

    def on_pet_lexi(self, _):
        emoji, message = random.choice(self.LEXI_PET_REACTIONS)
        self.mascot.pet()
        self.status_pill.set_text(message)

        def reset_mood():
            if not self.is_streaming:
                count = len(self.captured_pages)
                self.status_pill.set_text(f"✨ {count}/{self.max_pages} Pages" if count > 0 else "⚠️ No pages")
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(2500, reset_mood)
    on_pet_mochi = on_pet_lexi

    def trigger_drop_animation(self):
        if not hasattr(self, 'revealer'):
            return
        self.revealer.set_reveal_child(True)

    def animate_and_close(self):
        if getattr(self, '_is_closing', False):
            return
        self._is_closing = True
        if not hasattr(self, 'revealer'):
            self.close()
            return

        def on_revealed(rev, _):
            if not rev.get_reveal_child() and not rev.get_child_revealed():
                try:
                    rev.disconnect(handler_id)
                except Exception:
                    pass
                self.close()

        handler_id = self.revealer.connect('notify::child-revealed', on_revealed)
        self.revealer.set_reveal_child(False)
        # Safe fallback in case signal is dropped
        GLib.timeout_add(320, lambda: (self.close(), GLib.SOURCE_REMOVE)[1])

    def animate_and_minimize(self):
        if getattr(self, '_is_minimizing', False):
            return
        self._is_minimizing = True
        if not hasattr(self, 'revealer'):
            self.minimize()
            return

        def on_revealed(rev, _):
            if not rev.get_reveal_child() and not rev.get_child_revealed():
                try:
                    rev.disconnect(handler_id)
                except Exception:
                    pass
                self.minimize()
                self._is_minimizing = False

        handler_id = self.revealer.connect('notify::child-revealed', on_revealed)
        self.revealer.set_reveal_child(False)
        # Safe fallback in case signal is dropped
        GLib.timeout_add(320, lambda: (self.minimize(), setattr(self, '_is_minimizing', False), GLib.SOURCE_REMOVE)[2])

    def get_current_pos(self):
        surface = self.get_surface()
        if surface and hasattr(surface, 'get_xid'):
            try:
                xid = surface.get_xid()
                out = subprocess.check_output(['xdotool', 'getwindowgeometry', str(xid)], stderr=subprocess.DEVNULL).decode()
                m = re.search(r'Position:\s*(\d+),(\d+)', out)
                if m:
                    return int(m.group(1)), int(m.group(2))
            except Exception:
                pass
        return None

    def position_at_top(self):
        display = Gdk.Display.get_default()
        monitors = display.get_monitors() if display else None
        target_w = self.get_width() or 920

        screen_x = 0
        screen_y = 0
        screen_w = 1920
        if monitors and monitors.get_n_items() > 0:
            m = monitors.get_item(0)
            geom = m.get_geometry()
            screen_x = geom.x
            screen_y = geom.y
            screen_w = geom.width

        pos_x = screen_x + (screen_w - target_w) // 2
        pos_y = screen_y + 36  # Floating dynamic island below GNOME top bar
        self.window_pos = (pos_x, pos_y)
        self.saved_pos = (pos_x, pos_y)

        surface = self.get_surface()
        if surface and hasattr(surface, 'get_xid'):
            try:
                xid = surface.get_xid()
                subprocess.run(['xdotool', 'windowmove', str(xid), str(pos_x), str(pos_y)], check=False)
            except Exception as e:
                log(f"Window move exception: {e}")

    def build_ui(self):
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(260)
        self.revealer.set_vexpand(True)
        self.revealer.set_hexpand(True)
        self.revealer.set_reveal_child(False)
        self.set_content(self.revealer)

        # Main Card Container: Horizontal 2-Column Split
        self.card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.card.add_css_class('lexi-card')
        self.card.set_vexpand(True)
        self.card.set_hexpand(True)
        self.revealer.set_child(self.card)

        # Click anywhere on card background focuses entry
        click_gesture = Gtk.GestureClick()
        click_gesture.connect('pressed', lambda *_: self.entry.grab_focus())
        self.card.add_controller(click_gesture)

        # --- Left Companion Stage (Full-body Animated Lexi & Emotion Badge) ---
        self.stage_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.stage_box.set_valign(Gtk.Align.CENTER)
        self.stage_box.set_size_request(115, -1)
        self.stage_box.add_css_class('lexi-companion-stage')
        self.card.append(self.stage_box)

        self.avatar_btn = Gtk.Button()
        self.avatar_btn.set_focusable(False)
        self.avatar_btn.add_css_class('lexi-mascot-btn')
        self.avatar_btn.set_tooltip_text("Click to pet Lexi! ✨ (eyes follow your mouse)")

        self.mascot = LexiMascotWidget(width=115, height=130)
        self.avatar_btn.set_child(self.mascot)
        self.avatar_btn.connect('clicked', self.on_pet_lexi)
        self.stage_box.append(self.avatar_btn)

        self.status_pill = Gtk.Label(label="✂️ Snipping..." if self.is_snip else "✨ Ready")
        self.status_pill.add_css_class('lexi-status-pill')
        self.stage_box.append(self.status_pill)

        # --- Right Content Column (Wide Coucou-style Island) ---
        self.content_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.content_col.set_hexpand(True)
        self.card.append(self.content_col)

        # Header with draggable title and window controls
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.content_col.append(header)

        self.handle = Gtk.WindowHandle()
        self.handle.set_hexpand(True)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label="Lexi", xalign=0)
        title.add_css_class('lexi-title')
        self.subtitle = Gtk.Label(label="Cosmic Screen Copilot", xalign=0)
        self.subtitle.add_css_class('lexi-subtitle')
        title_box.append(title)
        title_box.append(self.subtitle)
        self.handle.set_child(title_box)
        header.append(self.handle)

        ctrls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.append(ctrls_box)

        self.clear_btn = Gtk.Button(label="🧹 Clear")
        self.clear_btn.set_focusable(False)
        self.clear_btn.set_tooltip_text("Clear chat conversation history")
        self.clear_btn.add_css_class('lexi-header-btn')
        self.clear_btn.connect('clicked', self.on_clear_chat)
        ctrls_box.append(self.clear_btn)

        self.minimize_btn = Gtk.Button.new_from_icon_name('window-minimize-symbolic')
        self.minimize_btn.set_focusable(False)
        self.minimize_btn.set_tooltip_text("Minimize window (Ctrl+M)")
        self.minimize_btn.add_css_class('flat')
        self.minimize_btn.add_css_class('lexi-win-btn')
        self.minimize_btn.connect('clicked', lambda _: self.animate_and_minimize())
        ctrls_box.append(self.minimize_btn)

        self.close_btn = Gtk.Button.new_from_icon_name('window-close-symbolic')
        self.close_btn.set_focusable(False)
        self.close_btn.set_tooltip_text("Close Lexi (Esc)")
        self.close_btn.add_css_class('flat')
        self.close_btn.add_css_class('lexi-win-btn')
        self.close_btn.connect('clicked', lambda _: self.animate_and_close())
        ctrls_box.append(self.close_btn)

        # Unified Wide Action Strip (Pages Tray + Quick Chips)
        action_strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        action_strip.add_css_class('lexi-action-strip')
        self.content_col.append(action_strip)

        self.pages_tray = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.pages_tray.add_css_class('lexi-pages-tray')
        action_strip.append(self.pages_tray)

        tray_lbl = Gtk.Label(label="Pages:")
        tray_lbl.add_css_class('lexi-tray-label')
        self.pages_tray.append(tray_lbl)

        pages_scroll = Gtk.ScrolledWindow()
        pages_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        pages_scroll.add_css_class('lexi-pages-scroll')
        pages_scroll.set_hexpand(False)
        pages_scroll.set_propagate_natural_width(True)
        self.pages_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pages_scroll.set_child(self.pages_box)
        self.pages_tray.append(pages_scroll)

        self.add_snip_btn = Gtk.Button(label="✂️ + Snip")
        self.add_snip_btn.set_focusable(False)
        self.add_snip_btn.set_tooltip_text("Snip an additional area (Lexi hides completely)")
        self.add_snip_btn.add_css_class('lexi-tray-btn')
        self.add_snip_btn.add_css_class('lexi-tray-btn-accent')
        self.add_snip_btn.connect('clicked', self.on_add_snip)
        self.pages_tray.append(self.add_snip_btn)

        self.add_full_btn = Gtk.Button(label="🖥️ + Screen")
        self.add_full_btn.set_focusable(False)
        self.add_full_btn.set_tooltip_text("Add entire screen as next page")
        self.add_full_btn.add_css_class('lexi-tray-btn')
        self.add_full_btn.connect('clicked', self.on_add_full)
        self.pages_tray.append(self.add_full_btn)

        self.recapture_btn = Gtk.Button(label="🔄 New")
        self.recapture_btn.set_focusable(False)
        self.recapture_btn.set_tooltip_text("Clear all pages and capture fresh screen")
        self.recapture_btn.add_css_class('lexi-tray-btn')
        self.recapture_btn.connect('clicked', self.on_recapture)
        self.pages_tray.append(self.recapture_btn)

        strip_sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        action_strip.append(strip_sep)

        chips_scroll = Gtk.ScrolledWindow()
        chips_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        chips_scroll.add_css_class('lexi-chips-scroll')
        chips_scroll.set_hexpand(True)
        chips_scroll.set_propagate_natural_width(True)
        chips_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        chips_scroll.set_child(chips_box)
        action_strip.append(chips_scroll)

        quick_prompts = [
            ("✨ Explain", "Explain what is on the attached page(s) clearly and concisely."),
            ("🔄 Compare", "Compare the information across all attached pages, highlighting differences or relationships."),
            ("🐛 Fix / Debug", "Identify any errors, bugs, or problems across these pages and provide the fix."),
            ("📝 Read Text", "Extract all key text, commands, or code from the attached pages."),
        ]

        for label_text, prompt_text in quick_prompts:
            btn = Gtk.Button(label=label_text)
            btn.set_focusable(False)
            btn.add_css_class('lexi-pill-btn')
            btn.connect('clicked', lambda _, p=prompt_text: self.on_quick_prompt(p))
            chips_box.append(btn)

        # Input Bar
        input_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        input_container.add_css_class('lexi-entry-box')
        self.content_col.append(input_container)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Ask anything about what's on your screen... (press Enter)")
        self.entry.set_hexpand(True)
        self.entry.set_focusable(True)
        self.entry.add_css_class('lexi-entry')
        self.entry.connect('activate', self.on_submit)
        input_container.append(self.entry)

        self.send_btn = Gtk.Button.new_from_icon_name('arrow-up-symbolic')
        self.send_btn.set_focusable(False)
        self.send_btn.add_css_class('lexi-send-btn')
        self.send_btn.connect('clicked', self.on_submit)
        input_container.append(self.send_btn)

        # Answer / Multi-Turn Response Area
        self.response_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.response_container.add_css_class('lexi-response-box')
        self.response_container.set_vexpand(True)
        self.content_col.append(self.response_container)

        # Thinking spinner bar (hidden by default)
        self.thinking_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.spinner = Gtk.Spinner()
        self.thinking_label = Gtk.Label(label="✨ Lexi is analyzing your screen...")
        self.thinking_label.add_css_class('lexi-thinking')
        self.thinking_box.append(self.spinner)
        self.thinking_box.append(self.thinking_label)
        self.thinking_box.set_visible(False)
        self.response_container.append(self.thinking_box)

        # Scrolled Text View with Markdown Tag Table
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.response_container.append(scrolled)

        tag_table = create_markdown_tag_table()
        self.text_buffer = Gtk.TextBuffer.new(tag_table)
        self.text_view = Gtk.TextView.new_with_buffer(self.text_buffer)
        self.text_view.set_editable(False)
        self.text_view.set_cursor_visible(False)
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text_view.add_css_class('lexi-answer-text')
        scrolled.set_child(self.text_view)

        init_hint = "✂️ Select an area to snip..." if self.is_snip else "👋 Looking at your screen right now..."
        self.text_buffer.set_text(init_hint)

        # Bottom Toolbar
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.response_container.append(bottom_bar)

        self.copy_btn = Gtk.Button(label="📋 Copy Answer")
        self.copy_btn.set_focusable(False)
        self.copy_btn.add_css_class('flat')
        self.copy_btn.add_css_class('lexi-action-btn')
        self.copy_btn.connect('clicked', self.on_copy)
        bottom_bar.append(self.copy_btn)

        bottom_spacer = Gtk.Box()
        bottom_spacer.set_hexpand(True)
        bottom_bar.append(bottom_spacer)

        dismiss_hint = Gtk.Label(label="Esc to close • Ctrl+M to minimize • Super+C to add page")
        dismiss_hint.add_css_class('lexi-subtitle')
        bottom_bar.append(dismiss_hint)

    def update_pages_tray_ui(self):
        # Clear existing cards
        while child := self.pages_box.get_first_child():
            self.pages_box.remove(child)

        for idx, page in enumerate(self.captured_pages, 1):
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            card.add_css_class('lexi-page-card')

            img = Gtk.Image.new_from_paintable(page["texture"])
            img.add_css_class('lexi-page-img')
            card.append(img)

            title_txt = f"Page {idx} (snip)" if page.get("is_snip") else f"Page {idx}"
            lbl = Gtk.Label(label=title_txt)
            lbl.add_css_class('lexi-page-title')
            card.append(lbl)

            del_btn = Gtk.Button.new_from_icon_name('window-close-symbolic')
            del_btn.set_focusable(False)
            del_btn.add_css_class('flat')
            del_btn.add_css_class('lexi-page-del-btn')
            del_btn.set_tooltip_text(f"Remove Page {idx}")
            del_btn.connect('clicked', lambda _, p_idx=idx-1: self.remove_page(p_idx))
            card.append(del_btn)

            self.pages_box.append(card)

        count = len(self.captured_pages)
        can_add = count < self.max_pages
        self.add_snip_btn.set_sensitive(can_add)
        self.add_full_btn.set_sensitive(can_add)

        if can_add:
            self.add_snip_btn.set_label(f"✂️ + Snip ({count}/5)")
            self.add_full_btn.set_label("🖥️ + Screen")
        else:
            self.add_snip_btn.set_label("✂️ Max (5/5)")
            self.add_full_btn.set_label("🖥️ Max (5/5)")

        if count == 0:
            self.status_pill.set_text("⚠️ No pages")
            if hasattr(self, 'subtitle'):
                self.subtitle.set_text("Cosmic Screen Copilot")
        else:
            self.status_pill.set_text(f"● {count}/5 Pages")
            if hasattr(self, 'subtitle'):
                self.subtitle.set_text(f"Cosmic Screen Copilot • {count}/5 Pages ready")

    def remove_page(self, page_index):
        if 0 <= page_index < len(self.captured_pages):
            self.captured_pages.pop(page_index)
            self.conversation = []  # Reset context when page set changes
            self.update_pages_tray_ui()
            count = len(self.captured_pages)
            if count == 0:
                self.text_buffer.set_text("⚠️ No pages attached. Click '✂️ + Snip' or '🖥️ + Screen' above to add a page.")
            else:
                self.text_buffer.set_text(f"📄 Page removed. {count} page(s) currently attached.")

    def capture_screen_and_reveal(self, interactive=False, append_mode=False):
        portal = Xdp.Portal()
        flags = Xdp.ScreenshotFlags.INTERACTIVE if interactive else Xdp.ScreenshotFlags.NONE

        def on_done(source, result, data):
            shot_path = None
            try:
                uri = portal.take_screenshot_finish(result)
                if uri:
                    parsed = urllib.parse.urlparse(uri)
                    p = urllib.parse.unquote(parsed.path)
                    if os.path.exists(p):
                        shot_path = p
            except Exception as e:
                log(f"Portal take_screenshot error: {e}")

            if shot_path:
                self.process_screenshot(shot_path, is_snip=interactive, append_mode=append_mode)
            else:
                self.status_pill.set_text("⚠️ Cancelled")
                if not self.captured_pages:
                    self.text_buffer.set_text("⚠️ Capture was not completed. Click '✂️ + Snip' or '🖥️ + Screen' above to try again!")

            # Completely restore window and focus entry
            self.set_visible(True)
            self.present()
            if self.saved_pos:
                surface = self.get_surface()
                if surface and hasattr(surface, 'get_xid'):
                    try:
                        xid = surface.get_xid()
                        subprocess.run(['xdotool', 'windowmove', str(xid), str(self.saved_pos[0]), str(self.saved_pos[1])], check=False)
                    except Exception:
                        pass
            else:
                self.position_at_top()

            self.trigger_drop_animation()
            self.set_focus(self.entry)
            GLib.idle_add(lambda: (self.entry.grab_focus(), GLib.SOURCE_REMOVE)[1])

            # Release hold if active from application startup
            if hasattr(self.app, 'has_startup_hold') and self.app.has_startup_hold:
                self.app.has_startup_hold = False
                self.app.release()

        portal.take_screenshot(None, flags, None, on_done, None)
        return GLib.SOURCE_REMOVE

    def on_add_snip(self, _):
        if len(self.captured_pages) >= self.max_pages:
            return
        self.saved_pos = self.get_current_pos()
        # Completely unmap window from Wayland so zero pointer events or pixels are blocked
        self.set_visible(False)
        GLib.timeout_add(150, lambda: self.capture_screen_and_reveal(interactive=True, append_mode=True))

    def on_add_full(self, _):
        if len(self.captured_pages) >= self.max_pages:
            return
        self.status_pill.set_text("⏳ Seeing screen...")
        self.saved_pos = self.get_current_pos()
        self.set_visible(False)
        GLib.timeout_add(250, lambda: self.capture_screen_and_reveal(interactive=False, append_mode=True))

    def on_recapture(self, _):
        self.status_pill.set_text("⏳ Seeing screen...")
        self.saved_pos = self.get_current_pos()
        self.set_visible(False)
        GLib.timeout_add(250, lambda: self.capture_screen_and_reveal(interactive=False, append_mode=False))

    def on_clear_chat(self, _):
        self.conversation = []
        self.last_answer = ""
        count = len(self.captured_pages)
        self.text_buffer.set_text(f"👋 Chat history cleared. What would you like to ask about the {count} attached page(s)?")
        self.entry.set_text("")
        self.entry.grab_focus()

    def process_screenshot(self, shot_path, is_snip=False, append_mode=False):
        try:
            with Image.open(shot_path) as im:
                # 1. Preview thumbnail (compact for up to 5 cards)
                thumb = im.copy()
                thumb.thumbnail((60, 38), Image.Resampling.LANCZOS)
                thumb_bytes = io.BytesIO()
                thumb.save(thumb_bytes, format='PNG')
                gbytes = GLib.Bytes.new(thumb_bytes.getvalue())
                texture = Gdk.Texture.new_from_bytes(gbytes)

                # 2. Scale image for LLM payload (max 800px, JPEG 65)
                max_dim = self.config.get('max_image_dimension', 800)
                quality = self.config.get('image_quality', 65)
                w, h = im.size
                if max(w, h) > max_dim:
                    im.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

                rgb_im = im.convert('RGB')
                buf = io.BytesIO()
                rgb_im.save(buf, format='JPEG', quality=quality)
                b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
                log(f"Processed {'snip' if is_snip else 'fullscreen'} ({im.size}), b64 size: {len(b64)}")

            try:
                os.remove(shot_path)
            except OSError:
                pass

            page_data = {
                "b64": b64,
                "texture": texture,
                "is_snip": is_snip
            }

            if append_mode and len(self.captured_pages) < self.max_pages:
                self.captured_pages.append(page_data)
                welcome = f"📸 Page {len(self.captured_pages)} attached! ({len(self.captured_pages)}/5 pages ready). Type your question or add another."
            else:
                self.captured_pages = [page_data]
                self.conversation = []
                welcome = "✂️ Page 1 snip captured! Click '✂️ + Snip' to attach up to 5 pages, or type your question." if is_snip else "👋 Page 1 screen seen! Click '✂️ + Snip' to attach up to 5 pages, or type your question."

            self.update_pages_tray_ui()
            self.text_buffer.set_text(welcome)
        except Exception as e:
            log(f"Error processing screenshot: {e}")
            self.status_pill.set_text("⚠️ No screen")

    def on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.animate_and_close()
            return True
        if (state & Gdk.ModifierType.CONTROL_MASK) and (keyval in (Gdk.KEY_m, Gdk.KEY_M)):
            self.animate_and_minimize()
            return True
        return False

    def on_quick_prompt(self, prompt_text):
        self.entry.set_text(prompt_text)
        self.on_submit(None)

    def scroll_to_end(self):
        mark = self.text_buffer.create_mark(None, self.text_buffer.get_end_iter(), False)
        self.text_view.scroll_to_mark(mark, 0.0, False, 0.0, 0.0)
        self.text_buffer.delete_mark(mark)

    def on_submit(self, _):
        text = self.entry.get_text().strip()
        if not text or self.is_streaming:
            return

        if not self.captured_pages:
            self.text_buffer.set_text("⚠️ No pages attached! Please click '✂️ + Snip' or '🖥️ + Screen' above to attach at least one page.")
            return

        self.is_streaming = True
        self.send_btn.set_sensitive(False)
        self.mascot.set_mood("working")
        self.status_pill.set_text("⚡ Analyzing")
        self.thinking_label.set_text("✨ Lexi is analyzing your screen... ✦ ✧ ✧")
        self.thinking_box.set_visible(True)
        self.spinner.start()
        self.query_start_time = time.time()

        if self.timer_source_id:
            GLib.source_remove(self.timer_source_id)
        self.timer_source_id = GLib.timeout_add(450, self.update_thinking_timer)

        # Multi-turn formatting in text buffer
        if len(self.conversation) == 0:
            self.text_buffer.set_text("")
        else:
            self.text_buffer.insert_with_tags_by_name(
                self.text_buffer.get_end_iter(),
                "\n────────────────────────────────────────\n\n",
                "divider"
            )

        self.text_buffer.insert_with_tags_by_name(self.text_buffer.get_end_iter(), "👤 You: ", "user_header")
        self.text_buffer.insert_with_tags_by_name(self.text_buffer.get_end_iter(), f"{text}\n\n", "bold")
        self.text_buffer.insert_with_tags_by_name(self.text_buffer.get_end_iter(), "✨ Lexi:\n", "lexi_header")

        if self.turn_start_mark:
            self.text_buffer.delete_mark(self.turn_start_mark)
        self.turn_start_mark = self.text_buffer.create_mark("turn_start", self.text_buffer.get_end_iter(), True)

        self.scroll_to_end()
        self.entry.set_text("")

        # Run query in background thread
        thread = threading.Thread(target=self.run_query, args=(text,))
        thread.daemon = True
        thread.start()

    def update_thinking_timer(self):
        if not self.is_streaming:
            return GLib.SOURCE_REMOVE
        elapsed = time.time() - self.query_start_time
        pages_count = len(self.captured_pages)

        # Animated stardust sparkle dots
        dot_cycles = ["✦ ✧ ✧", "✧ ✦ ✧", "✧ ✧ ✦", "✦ ✦ ✦"]
        step = int(elapsed * 2.2)
        dots = dot_cycles[step % len(dot_cycles)]

        self.thinking_label.set_text(f"✨ Lexi exploring {pages_count} page(s)... {dots} ({int(elapsed)}s)")
        return GLib.SOURCE_CONTINUE

    def get_payload_messages(self, current_user_question):
        msgs = []
        if self.config.get('system_prompt'):
            msgs.append({"role": "system", "content": self.config.get('system_prompt')})

        # Keep history bounded (turn 0 + last 14 messages)
        conv = list(self.conversation)
        if len(conv) > 16:
            conv = conv[:2] + conv[-14:]
        msgs.extend(conv)

        # Turn 0 includes all attached pages; subsequent turns are lightweight text
        if not self.conversation:
            content = [{"type": "text", "text": current_user_question}]
            num_pages = len(self.captured_pages)
            for idx, page in enumerate(self.captured_pages, 1):
                if num_pages > 1:
                    content.append({
                        "type": "text",
                        "text": f"--- [Page {idx} of {num_pages}] ---"
                    })
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{page['b64']}"}
                })
            user_turn = {
                "role": "user",
                "content": content
            }
        else:
            user_turn = {
                "role": "user",
                "content": current_user_question
            }
        msgs.append(user_turn)
        return msgs, user_turn

    def run_query(self, user_question):
        full_response = []
        user_turn = None
        try:
            api_base = self.config.get('api_base', DEFAULT_CONFIG['api_base']).rstrip('/')
            api_key = self.config.get('api_key', '')
            models_list = self.config.get('models')
            model = self.config.get('model', 'google/gemini-2.5-flash-lite')
            timeout = self.config.get('request_timeout', 35)

            if not self.captured_pages:
                GLib.idle_add(self.append_stream_token, "⚠️ No pages attached. Please click '✂️ + Snip' or '🖥️ + Screen' above to attach a page!")
                return

            messages, user_turn = self.get_payload_messages(user_question)

            payload = {
                "messages": messages,
                "stream": True,
                "max_tokens": self.config.get('max_tokens', 1000),
                "route": "fallback"
            }
            if models_list and isinstance(models_list, list):
                payload["models"] = models_list
            else:
                payload["model"] = model

            url = f"{api_base}/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://lexi.desktop",
                "X-Title": "Lexi Screen Copilot"
            }

            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers)
            with DIRECT_OPENER.open(req, timeout=timeout) as resp:
                GLib.idle_add(self.on_query_started)
                for line in resp:
                    line = line.decode('utf-8').strip()
                    if line.startswith('data: ') and line != 'data: [DONE]':
                        try:
                            chunk = json.loads(line[6:])
                            served_model = chunk.get('model')
                            if served_model:
                                short_name = served_model.split('/')[-1]
                                GLib.idle_add(self.status_pill.set_text, f"● {short_name}")
                            delta = chunk['choices'][0]['delta'].get('content', '')
                            if delta:
                                full_response.append(delta)
                                GLib.idle_add(self.append_stream_token, delta)
                        except Exception:
                            pass
        except Exception as e:
            log(f"run_query error: {e}")
            err_msg = f"\n⚠️ Error connecting: {e}\n(Tip: Press Enter to retry or check your connection)"
            full_response.append(err_msg)
            GLib.idle_add(self.append_stream_token, err_msg)
        finally:
            full_text = "".join(full_response)
            GLib.idle_add(self.on_query_finished, full_text, user_turn)

    def on_query_started(self):
        if self.timer_source_id:
            GLib.source_remove(self.timer_source_id)
            self.timer_source_id = None
        self.spinner.stop()
        self.thinking_box.set_visible(False)

    def append_stream_token(self, delta):
        end_iter = self.text_buffer.get_end_iter()
        self.text_buffer.insert(end_iter, delta)
        self.scroll_to_end()

    def on_query_finished(self, full_text, user_turn):
        if self.timer_source_id:
            GLib.source_remove(self.timer_source_id)
            self.timer_source_id = None
        self.spinner.stop()
        self.thinking_box.set_visible(False)
        self.is_streaming = False
        self.send_btn.set_sensitive(True)

        if full_text and self.turn_start_mark:
            # Replace raw stream with beautiful formatted markdown
            start_iter = self.text_buffer.get_iter_at_mark(self.turn_start_mark)
            end_iter = self.text_buffer.get_end_iter()
            self.text_buffer.delete(start_iter, end_iter)

            render_markdown_to_buffer(self.text_buffer, full_text)
            self.text_buffer.delete_mark(self.turn_start_mark)
            self.turn_start_mark = None

            if user_turn and not full_text.startswith("\n⚠️ Error"):
                self.conversation.append(user_turn)
                self.conversation.append({"role": "assistant", "content": full_text})
                self.last_answer = full_text

        self.scroll_to_end()
        self.entry.grab_focus()

        # Celebratory mascot reaction when answer completes
        if full_text and not full_text.startswith("\n⚠️ Error"):
            self.mascot.set_mood("happy")
            self.status_pill.set_text("🌟 Cosmic answer ready! ✨")
            def reset_celebration():
                self.mascot.reset_mood()
                if not self.is_streaming:
                    count = len(self.captured_pages)
                    self.status_pill.set_text(f"✨ {count}/{self.max_pages} Pages" if count > 0 else "⚠️ No pages")
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(2500, reset_celebration)

    def on_copy(self, _):
        text_to_copy = self.last_answer
        if not text_to_copy:
            start = self.text_buffer.get_start_iter()
            end = self.text_buffer.get_end_iter()
            text_to_copy = self.text_buffer.get_text(start, end, True)

        if text_to_copy:
            display = Gdk.Display.get_default()
            clipboard = display.get_clipboard()
            clipboard.set(text_to_copy)
            self.copy_btn.set_label("Copied! ✨🚀")
            self.mascot.set_mood("happy")
            def reset_copy():
                self.copy_btn.set_label("📋 Copy Answer")
                if not self.is_streaming:
                    self.mascot.reset_mood()
                return GLib.SOURCE_REMOVE
            GLib.timeout_add_seconds(2, reset_copy)


class LexiApp(Adw.Application):
    def __init__(self, config):
        super().__init__(
            application_id='org.antigravity.LexiCompanion',
            flags=gi.repository.Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        )
        self.config = config
        self.active_window = None
        self.has_startup_hold = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.load_css()

    def load_css(self):
        if os.path.exists(STYLES_FILE):
            provider = Gtk.CssProvider()
            provider.load_from_path(STYLES_FILE)
            display = Gdk.Display.get_default()
            if display:
                Gtk.StyleContext.add_provider_for_display(
                    display,
                    provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )

    def do_activate(self):
        if self.active_window and hasattr(self.active_window, 'captured_pages'):
            win = self.active_window
            win.set_visible(True)
            win.present()
            if not win.saved_pos:
                win.position_at_top()
            win.trigger_drop_animation()
            win.entry.grab_focus()
        else:
            self.hold()
            self.has_startup_hold = True
            win = LexiWindow(self, self.config, is_snip=False)
            self.active_window = win
            win.set_visible(False)
            GLib.timeout_add(150, lambda: win.capture_screen_and_reveal(interactive=False, append_mode=False))

    def do_command_line(self, command_line):
        args = command_line.get_arguments()
        if '--help' in args or '-h' in args:
            print("Lexi Copilot - Cosmic Screen Companion\nUsage:\n  lexi-copilot [options]\n\nOptions:\n  -s, --snip    Interactive region snip\n  -h, --help    Show this help message")
            return 0

        is_snip = ('--snip' in args) or ('-s' in args)

        if self.active_window and hasattr(self.active_window, 'captured_pages'):
            win = self.active_window
            if len(win.captured_pages) < win.max_pages:
                # Append page seamlessly
                win.saved_pos = win.get_current_pos()
                win.set_visible(False)
                if hasattr(win, 'revealer'):
                    win.revealer.set_reveal_child(False)
                GLib.timeout_add(150, lambda: win.capture_screen_and_reveal(interactive=is_snip, append_mode=True))
            else:
                # At max pages, bring window back up
                win.set_visible(True)
                win.present()
                win.trigger_drop_animation()
                win.entry.grab_focus()
        else:
            # First launch: keep hold until window reveals
            self.hold()
            self.has_startup_hold = True
            win = LexiWindow(self, self.config, is_snip=is_snip)
            self.active_window = win
            win.set_visible(False)
            GLib.timeout_add(150, lambda: win.capture_screen_and_reveal(interactive=is_snip, append_mode=False))

        return 0


# Backwards compatibility aliases
MochiWindow = LexiWindow
MochiApp = LexiApp


def main():
    log("=== Lexi invoked ===")
    config = load_config()
    app = LexiApp(config)
    return app.run(sys.argv)

if __name__ == '__main__':
    sys.exit(main())



