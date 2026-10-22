#!/usr/bin/env python3
"""
Generates the skin files of the TV interface (home window):

- resources/skins/default/1080i/script-mediathekview-home.xml
- resources/skins/default/media/mv_*.png
- resources/skins/default/media/logos/*.png (channel logos cropped from resources/icons, needs Pillow)

Kodi windows cannot define their own includes, and the ten content rows of
the home window use identical tile layouts, so the window XML is generated
from the templates below. Re-run this script after changing them:

    python3 .scripts/generate_home_window.py
"""

import glob
import math
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIN = os.path.join(ROOT, 'resources', 'skins', 'default')
XML_PATH = os.path.join(SKIN, '1080i', 'script-mediathekview-home.xml')
MEDIA = os.path.join(SKIN, 'media')
ICONS = os.path.join(ROOT, 'resources', 'icons')

ADDON = 'plugin.video.mediathekview'
NUM_ROWS = 10

# Colours (AARRGGBB): night blue background like the MediathekView icon, yellow accent
BACKGROUND_RGB = (9, 18, 33)
BACKGROUND = 'FF091221'
TEXT = 'FFFFFFFF'
TEXT_DIM = 'FF8FA3BF'
TEXT_DARK = 'FF091221'
ACCENT = 'FFFFC61A'
MESSAGE = 'FFFFDB75'
TILE = 'FF172338'
TILE_FOCUSED = 'FF243552'

NAV_WIDTH = 190

# Tiles of the rows (16:9)
TILE_WIDTH = 410
THUMB_LEFT = 12
THUMB_TOP = 12
THUMB_WIDTH = 384
THUMB_HEIGHT = 216
TILE_HEIGHT = THUMB_TOP + THUMB_HEIGHT + 50

# Titles of the film tiles, wrapped by the add-on: line height of font13 (rows) and font12 (grid)
TITLE_MAX_LINES = 3
TITLE_LINE_ROW = 41
TITLE_LINE_GRID = 34
# names of the shows in bold: font14 (rows) and font13 (grid)
SHOW_LINE_ROW = 45
SHOW_LINE_GRID = 41

# Tiles of the grid
GRID_COLUMNS = 5
GRID_TILE_WIDTH = 360
GRID_THUMB_WIDTH = 336
GRID_THUMB_HEIGHT = 189
GRID_TILE_HEIGHT = THUMB_TOP + GRID_THUMB_HEIGHT + 50

# Logos: large for the preview, small for the tiles (twice the size they are shown at,
# Kodi does not smooth images it scales down a lot)
LOGO_SIZE = (640, 260)
LOGO_SMALL_SIZE = (300, 84)


# ---------------------------------------------------------------------------
# Textures
# ---------------------------------------------------------------------------

def write_png(path, width, height, pixel):
    """Write an RGBA PNG, pixel(x, y) returns (r, g, b, a)"""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixel(x, y))

    def chunk(kind, payload):
        return (struct.pack('>I', len(payload))
                + kind
                + payload
                + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff))

    png = b''.join((
        b'\x89PNG\r\n\x1a\n',
        chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)),
        chunk(b'IDAT', zlib.compress(bytes(raw), 9)),
        chunk(b'IEND', b''),
    ))
    with open(path, 'wb') as png_file:
        png_file.write(png)


def rounded_coverage(x, y, width, height, radius, samples=4):
    """Anti-aliased coverage (0..1) of a pixel within a rounded rectangle"""
    inside = 0
    for sub_y in range(samples):
        for sub_x in range(samples):
            px = x + (sub_x + 0.5) / samples
            py = y + (sub_y + 0.5) / samples
            cx = min(max(px, radius), width - radius)
            cy = min(max(py, radius), height - radius)
            if (px - cx) ** 2 + (py - cy) ** 2 <= radius ** 2:
                inside += 1
    return inside / (samples * samples)


def rounded(width, height, radius, outline=0):
    def pixel(x, y):
        alpha = rounded_coverage(x, y, width, height, radius)
        if outline:
            inner = rounded_coverage(x - outline, y - outline, width - 2 * outline, height - 2 * outline,
                                     max(radius - outline, 1))
            if outline <= x < width - outline and outline <= y < height - outline:
                alpha = max(0.0, alpha - inner)
        return 255, 255, 255, int(round(255 * alpha))

    return pixel


def shade(width, height, radius):
    """Rounded tile overlay: light at the top, dark at the bottom where the title is"""
    def pixel(x, y):
        coverage = rounded_coverage(x, y, width, height, radius)
        t = y / float(height - 1)
        top = max(0.0, 0.25 - t) * 0.5          # a little light at the top edge
        dark = max(0.0, (t - 0.25) / 0.75) ** 1.4 * 0.62
        if top > 0:
            return 255, 255, 255, int(round(255 * coverage * top))
        return 0, 0, 0, int(round(255 * coverage * dark))

    return pixel


def photo_shade(width, height, radius):
    """Overlay of tiles with an image: darker at the top for the logo and at the bottom for the title"""
    def pixel(x, y):
        coverage = rounded_coverage(x, y, width, height, radius)
        t = y / float(height - 1)
        alpha = max(0.0, (0.3 - t) / 0.3) ** 1.5 * 0.45 + max(0.0, (t - 0.3) / 0.7) ** 1.3 * 0.85
        return 0, 0, 0, int(round(255 * coverage * min(alpha, 1.0)))

    return pixel


def glow(size):
    """Radial gradient, tinted with the channel colour behind the preview"""
    centre = (size - 1) / 2.0

    def pixel(x, y):
        distance = math.hypot(x - centre, y - centre) / centre
        alpha = max(0.0, 1.0 - distance) ** 1.8 * 0.85
        return 255, 255, 255, int(round(255 * alpha))

    return pixel


def generate_textures():
    if not os.path.isdir(MEDIA):
        os.makedirs(MEDIA)
    r, g, b = BACKGROUND_RGB
    write_png(os.path.join(MEDIA, 'mv_white.png'), 8, 8, lambda x, y: (255, 255, 255, 255))
    # 9-slice textures, used with border="12" / border="28" / border="16"
    write_png(os.path.join(MEDIA, 'mv_rounded.png'), 64, 64, rounded(64, 64, 10))
    write_png(os.path.join(MEDIA, 'mv_pill.png'), 64, 64, rounded(64, 64, 31))
    write_png(os.path.join(MEDIA, 'mv_focus.png'), 64, 64, rounded(64, 64, 14, outline=4))
    # overlays of the coloured tiles in the exact tile sizes
    write_png(os.path.join(MEDIA, 'mv_shade.png'), THUMB_WIDTH, THUMB_HEIGHT, shade(THUMB_WIDTH, THUMB_HEIGHT, 10))
    write_png(os.path.join(MEDIA, 'mv_shade_grid.png'), GRID_THUMB_WIDTH, GRID_THUMB_HEIGHT,
              shade(GRID_THUMB_WIDTH, GRID_THUMB_HEIGHT, 10))
    write_png(os.path.join(MEDIA, 'mv_photo_shade.png'), THUMB_WIDTH, THUMB_HEIGHT, photo_shade(THUMB_WIDTH, THUMB_HEIGHT, 10))
    write_png(os.path.join(MEDIA, 'mv_photo_shade_grid.png'), GRID_THUMB_WIDTH, GRID_THUMB_HEIGHT,
              photo_shade(GRID_THUMB_WIDTH, GRID_THUMB_HEIGHT, 10))
    # alpha masks for rounded images
    write_png(os.path.join(MEDIA, 'mv_mask.png'), THUMB_WIDTH, THUMB_HEIGHT, rounded(THUMB_WIDTH, THUMB_HEIGHT, 10))
    write_png(os.path.join(MEDIA, 'mv_mask_grid.png'), GRID_THUMB_WIDTH, GRID_THUMB_HEIGHT,
              rounded(GRID_THUMB_WIDTH, GRID_THUMB_HEIGHT, 10))
    write_png(os.path.join(MEDIA, 'mv_glow.png'), 256, 256, glow(256))
    # gradients blending the preview into the background
    write_png(os.path.join(MEDIA, 'mv_gradient_left.png'), 256, 4, lambda x, y: (r, g, b, int(255 * (1 - x / 255.0) ** 1.3)))
    write_png(os.path.join(MEDIA, 'mv_gradient_bottom.png'), 4, 256, lambda x, y: (r, g, b, int(255 * (y / 255.0) ** 1.2)))


def generate_logos():
    """Channel logos without the empty border of the icons, in two sizes"""
    try:
        from PIL import Image
    except ImportError:
        print('Pillow is missing, the logos were not generated')
        return
    for size, folder in ((LOGO_SIZE, os.path.join(MEDIA, 'logos')), (LOGO_SMALL_SIZE, os.path.join(MEDIA, 'logos', 'small'))):
        if not os.path.isdir(folder):
            os.makedirs(folder)
        for path in sorted(glob.glob(os.path.join(ICONS, 'sender', '*-i.png')) + glob.glob(os.path.join(ICONS, 'livestream', '*-i.png'))):
            name = os.path.basename(path)[:-len('-i.png')]
            if name in ('radio bremen tv', 'srf.podcast') or name.startswith('arte.') and name != 'arte.de':
                # same logo as rbtv, srf and arte.de
                continue
            image = Image.open(path).convert('RGBA')
            image = image.crop(image.getchannel('A').getbbox())
            scale = min(size[0] / float(image.width), size[1] / float(image.height))
            image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.LANCZOS)
            image.save(os.path.join(folder, name + '.png'), optimize=True)


# ---------------------------------------------------------------------------
# Window XML
# ---------------------------------------------------------------------------

def localize(string_id):
    return '$ADDON[%s %d]' % (ADDON, string_id)


def is_kind(*kinds, **kwargs):
    prefix = kwargs.get('prefix', '')
    return '[' + ' | '.join('String.IsEqual(%sListItem.Property(kind),%s)' % (prefix, kind) for kind in kinds) + ']'


TEXT_KINDS = ('action', 'all', 'next', 'back')
CARD_KINDS = ('film', 'live', 'channel', 'show', 'letter')


def title_layout(kind, lines, small, thumb_width, thumb_height, pad):
    """
    Title of a film or show tile for a title of that many lines, wrapped by the add-on
    (resources/lib/home/data.py: wrap_title): the title ends at the bottom of the tile, the
    name of the show of a film is right above it
    """
    if kind == 'film':
        font, line_height = ('font12', TITLE_LINE_GRID) if small else ('font13', TITLE_LINE_ROW)
    else:
        font, line_height = ('font13', SHOW_LINE_GRID) if small else ('font14', SHOW_LINE_ROW)
    # three lines need the space down to the bottom and up to the logo
    bottom = THUMB_TOP + thumb_height - (16 if small else 18) if lines < 3 else THUMB_TOP + thumb_height - (12 if small else 6)
    title_top = bottom - lines * line_height
    # the shade of the image only darkens the lower part, three lines reach up into the bright part
    dim = '''
                <control type="image">
                    <left>{tl}</left><top>{tt}</top><width>{tw}</width><height>{th}</height>
                    <texture border="12" colordiffuse="59000000">mv_rounded.png</texture>
                </control>'''.format(tl=THUMB_LEFT, tt=THUMB_TOP, tw=thumb_width, th=thumb_height) if lines == 3 else ''
    show = '''
                <control type="label">
                    <left>{left}</left><top>{top}</top><width>{width}</width><height>30</height>
                    <font>font10</font>
                    <textcolor>D9FFFFFF</textcolor>
                    <label>$INFO[ListItem.Property(tile_top)]</label>
                </control>'''.format(left=THUMB_LEFT + pad, top=title_top - (26 if small else 28),
                                     width=thumb_width - 2 * pad) if kind == 'film' else ''
    return '''
            <control type="group">
                <visible>{kind} + String.IsEqual(ListItem.Property(title_lines),{lines})</visible>{dim}{show}
                <control type="textbox">
                    <left>{left}</left><top>{title_top}</top><width>{title_width}</width><height>{title_height}</height>
                    <font>{font}</font>
                    <textcolor>{text}</textcolor>
                    <shadowcolor>66000000</shadowcolor>
                    <label>{bold}$INFO[ListItem.Property(title)]{bold_end}</label>
                </control>
            </control>'''.format(
        kind=is_kind(kind), lines=lines, dim=dim, show=show, left=THUMB_LEFT + pad,
        bold='[B]' if kind == 'show' else '', bold_end='[/B]' if kind == 'show' else '',
        title_top=title_top, font=font, text=TEXT,
        # wider than the wrapped lines, Kodi must not wrap them again
        title_width=thumb_width - 2 * pad + 12, title_height=lines * line_height + 6)


def tile_layout(list_id, focused, width, thumb_width, thumb_height, shade_texture):
    """Layout of one tile; list_id is used for focus dependent styling"""
    has_focus = 'Control.HasFocus(%d)' % list_id
    zoom = focus_ring = ''
    if focused:
        zoom = ('<animation effect="zoom" end="106" center="{cx},{cy}" time="150" tween="sine" condition="{focus}">'
                'Conditional</animation>').format(cx=THUMB_LEFT + thumb_width // 2, cy=THUMB_TOP + thumb_height // 2,
                                                  focus=has_focus)
        focus_ring = '''
            <control type="image">
                <left>{left}</left><top>{top}</top><width>{width}</width><height>{height}</height>
                <texture border="16" colordiffuse="FFFFFFFF">mv_focus.png</texture>
                <visible>{focus}</visible>
            </control>'''.format(left=THUMB_LEFT - 6, top=THUMB_TOP - 6, width=thumb_width + 12,
                                 height=thumb_height + 12, focus=has_focus)
    small = thumb_width < THUMB_WIDTH
    pad = 16 if small else 18
    logo_height = 34 if small else 40
    film_title = ''.join(title_layout('film', lines, small, thumb_width, thumb_height, pad)
                         for lines in range(1, TITLE_MAX_LINES + 1))
    show_title = ''.join(title_layout('show', lines, small, thumb_width, thumb_height, pad)
                         for lines in range(1, TITLE_MAX_LINES + 1))
    # line below the tile: the name of livestreams, date and duration of films, counts of the others
    below = '''
            <control type="label">
                <left>{xl}</left><top>{xt}</top><width>{xw}</width><height>34</height>
                <font>font12</font>
                <textcolor>{colour}</textcolor>
                <scroll>{scroll}</scroll>
                <label>$INFO[ListItem.Label2]</label>
                <visible>!{live} + !{text_tile}</visible>
            </control>
            <control type="label">
                <left>{xl}</left><top>{xt}</top><width>{xw}</width><height>34</height>
                <font>font12</font>
                <textcolor>{colour}</textcolor>
                <scroll>{scroll}</scroll>
                <label>$INFO[ListItem.Label]</label>
                <visible>{live}</visible>
            </control>'''.format(xl=THUMB_LEFT + 2, xt=THUMB_TOP + thumb_height + 10, xw=thumb_width - 4,
                                 colour=TEXT if focused else TEXT_DIM, scroll='true' if focused else 'false',
                                 live=is_kind('live'), text_tile=is_kind(*TEXT_KINDS))

    return '''
        <control type="group">
            {zoom}
            <control type="image">
                <left>{tl}</left><top>{tt}</top><width>{tw}</width><height>{th}</height>
                <texture border="12" colordiffuse="$INFO[ListItem.Property(color)]">mv_rounded.png</texture>
                <visible>{card}</visible>
            </control>
            <control type="image">
                <left>{tl}</left><top>{tt}</top><width>{tw}</width><height>{th}</height>
                <texture>{shade}</texture>
                <visible>{card} + String.IsEmpty(ListItem.Property(image))</visible>
            </control>
            <control type="group">
                <visible>{film_or_show} + !String.IsEmpty(ListItem.Property(image))</visible>
                <control type="image">
                    <left>{tl}</left><top>{tt}</top><width>{tw}</width><height>{th}</height>
                    <aspectratio>scale</aspectratio>
                    <fadetime>200</fadetime>
                    <texture background="true" diffuse="{mask}">$INFO[ListItem.Property(image)]</texture>
                </control>
                <control type="image">
                    <left>{tl}</left><top>{tt}</top><width>{tw}</width><height>{th}</height>
                    <texture>{photo_shade}</texture>
                </control>
            </control>
            <control type="group">
                <visible>{film_or_show}</visible>
                <control type="image">
                    <left>{pl}</left><top>{lt}</top><width>{lw}</width><height>{lh}</height>
                    <aspectratio align="left" aligny="top">keep</aspectratio>
                    <texture background="true">$INFO[ListItem.Property(logo_small)]</texture>
                    <visible>!{three_lines}</visible>
                </control>
                <control type="image">
                    <left>{pl}</left><top>{lt}</top><width>{lw3}</width><height>{lh3}</height>
                    <aspectratio align="left" aligny="top">keep</aspectratio>
                    <texture background="true">$INFO[ListItem.Property(logo_small)]</texture>
                    <visible>{three_lines}</visible>
                </control>
                <control type="label">
                    <left>{pl}</left><top>{lt}</top><width>{pw}</width><height>{lh}</height>
                    <aligny>center</aligny>
                    <font>font12</font>
                    <textcolor>{text}</textcolor>
                    <label>[B]$INFO[ListItem.Property(channel_name)][/B]</label>
                    <visible>String.IsEmpty(ListItem.Property(logo_small)) + {film}</visible>
                </control>
                <control type="label">
                    <left>{pl}</left><top>{lt}</top><width>{pw}</width><height>{lh}</height>
                    <aligny>center</aligny>
                    <font>font10</font>
                    <textcolor>D9FFFFFF</textcolor>
                    <label>$INFO[ListItem.Property(tile_top)]</label>
                    <visible>String.IsEmpty(ListItem.Property(logo_small)) + {show}</visible>
                </control>
            </control>
{film_title}
{show_title}
            <control type="group">
                <visible>{live_or_channel}</visible>
                <control type="image">
                    <left>{cl}</left><top>{ct}</top><width>{cw}</width><height>{ch}</height>
                    <aspectratio>keep</aspectratio>
                    <texture background="true">$INFO[ListItem.Property(logo)]</texture>
                </control>
                <control type="label">
                    <left>{pl}</left><top>{tt}</top><width>{pw}</width><height>{th}</height>
                    <align>center</align><aligny>center</aligny>
                    <font>font30_title</font>
                    <textcolor>{text}</textcolor>
                    <label>$INFO[ListItem.Label]</label>
                    <visible>String.IsEmpty(ListItem.Property(logo))</visible>
                </control>
            </control>
            <control type="label">
                <left>{tl}</left><top>{tt}</top><width>{tw}</width><height>{th}</height>
                <align>center</align><aligny>center</aligny>
                <font>font45_title</font>
                <textcolor>{text}</textcolor>
                <label>$INFO[ListItem.Label]</label>
                <visible>{letter}</visible>
            </control>
            <control type="image">
                <left>{tl}</left><top>{tt}</top><width>{tw}</width><height>{th}</height>
                <texture border="12" colordiffuse="{tile_focused}">mv_rounded.png</texture>
                <visible>{text_tile}</visible>
            </control>
            <control type="label">
                <left>{pl}</left><top>{tt}</top><width>{pw}</width><height>{th}</height>
                <align>center</align><aligny>center</aligny>
                <font>font14</font>
                <textcolor>{text}</textcolor>
                <label>$INFO[ListItem.Label]</label>
                <visible>String.IsEqual(ListItem.Property(kind),action)</visible>
            </control>
            <control type="label">
                <left>{pl}</left><top>{tt}</top><width>{pw}</width><height>{th}</height>
                <align>center</align><aligny>center</aligny>
                <font>font14</font>
                <textcolor>{text}</textcolor>
                <label>$INFO[ListItem.Label]  &#x203A;</label>
                <visible>String.IsEqual(ListItem.Property(kind),all) | String.IsEqual(ListItem.Property(kind),next)</visible>
            </control>
            <control type="label">
                <left>{pl}</left><top>{tt}</top><width>{pw}</width><height>{th}</height>
                <align>center</align><aligny>center</aligny>
                <font>font14</font>
                <textcolor>{text}</textcolor>
                <label>&#x2039;  $INFO[ListItem.Label]</label>
                <visible>String.IsEqual(ListItem.Property(kind),back)</visible>
            </control>
            <control type="progress">
                <left>{pl}</left><top>{prt}</top><width>{pw}</width><height>6</height>
                <texturebg colordiffuse="55FFFFFF">mv_white.png</texturebg>
                <midtexture colordiffuse="{accent}">mv_white.png</midtexture>
                <info>ListItem.PercentPlayed</info>
                <visible>!String.IsEmpty(ListItem.Property(progress))</visible>
            </control>
            <control type="group">
                <left>{badge_left}</left><top>{badge_top}</top>
                <visible>String.IsEqual(ListItem.Property(live),true)</visible>
                <control type="image">
                    <width>76</width><height>30</height>
                    <texture border="12" colordiffuse="FFE0263C">mv_rounded.png</texture>
                </control>
                <control type="label">
                    <width>76</width><height>30</height>
                    <align>center</align><aligny>center</aligny>
                    <font>font10</font><textcolor>FFFFFFFF</textcolor>
                    <label>LIVE</label>
                </control>
            </control>
            {focus_ring}{below}
        </control>'''.format(
        zoom=zoom, focus_ring=focus_ring, below=below, shade=shade_texture,
        mask='mv_mask_grid.png' if small else 'mv_mask.png',
        photo_shade='mv_photo_shade_grid.png' if small else 'mv_photo_shade.png',
        card=is_kind(*CARD_KINDS), film=is_kind('film'), show=is_kind('show'), letter=is_kind('letter'),
        film_or_show=is_kind('film', 'show'), live_or_channel=is_kind('live', 'channel'), text_tile=is_kind(*TEXT_KINDS),
        tl=THUMB_LEFT, tt=THUMB_TOP, tw=thumb_width, th=thumb_height,
        pl=THUMB_LEFT + pad, pw=thumb_width - 2 * pad,
        lt=THUMB_TOP + pad - 2, lw=int(thumb_width * 0.42), lh=logo_height,
        film_title=film_title,
        # a smaller logo leaves room for titles of three lines
        three_lines='String.IsEqual(ListItem.Property(title_lines),3)',
        lw3=int(thumb_width * 0.42) * (logo_height - 10) // logo_height, lh3=logo_height - 10,
        show_title=show_title,
        cl=THUMB_LEFT + thumb_width // 5, ct=THUMB_TOP + thumb_height // 4, cw=thumb_width * 3 // 5, ch=thumb_height // 2,
        prt=THUMB_TOP + thumb_height - 12,
        badge_left=THUMB_LEFT + thumb_width - 76 - 12, badge_top=THUMB_TOP + 12,
        text=TEXT, accent=ACCENT, tile_focused=TILE_FOCUSED,
    )


def row(index):
    list_id = 301 + index
    return '''
            <control type="group" id="{group_id}">
                <width>1920</width><height>{height}</height>
                <control type="label" id="{label_id}">
                    <left>80</left><top>4</top><width>1600</width><height>46</height>
                    <font>font14</font>
                    <textcolor>{text}</textcolor>
                </control>
                <control type="list" id="{list_id}">
                    <left>68</left><top>50</top><width>1852</width><height>{tile_height}</height>
                    <orientation>horizontal</orientation>
                    <onup>{list_id}</onup>
                    <ondown>{list_id}</ondown>
                    <scrolltime tween="sine">200</scrolltime>
                    <itemlayout width="{tile_width}" height="{tile_height}">{item}
                    </itemlayout>
                    <focusedlayout width="{tile_width}" height="{tile_height}">{focused}
                    </focusedlayout>
                </control>
            </control>'''.format(
        group_id=501 + index, label_id=401 + index, list_id=list_id, text=TEXT,
        height=TILE_HEIGHT + 50, tile_width=TILE_WIDTH, tile_height=TILE_HEIGHT,
        item=tile_layout(list_id, False, TILE_WIDTH, THUMB_WIDTH, THUMB_HEIGHT, 'mv_shade.png'),
        focused=tile_layout(list_id, True, TILE_WIDTH, THUMB_WIDTH, THUMB_HEIGHT, 'mv_shade.png'),
    )


def pill_layout(list_id, focused, width, active_condition=None):
    """Tab bar entries and detail buttons"""
    has_focus = 'Control.HasFocus(%d)' % list_id
    parts = []
    if focused:
        parts.append('''
            <control type="image">
                <left>6</left><top>8</top><width>{w}</width><height>56</height>
                <texture border="28" colordiffuse="FFFFFFFF">mv_pill.png</texture>
                <visible>{focus}</visible>
            </control>
            <control type="label">
                <width>{width}</width><height>72</height>
                <align>center</align><aligny>center</aligny>
                <font>font13</font><textcolor>{dark}</textcolor>
                <label>$INFO[ListItem.Label]</label>
                <visible>{focus}</visible>
            </control>'''.format(w=width - 12, width=width, dark=TEXT_DARK, focus=has_focus))
    if active_condition is None:
        # buttons: translucent pill
        parts.append('''
            <control type="image">
                <left>6</left><top>8</top><width>{w}</width><height>56</height>
                <texture border="28" colordiffuse="33FFFFFF">mv_pill.png</texture>
                <visible>{hide}</visible>
            </control>'''.format(w=width - 12, hide='!' + has_focus if focused else 'true'))
    focus_condition = (' + !%s' % has_focus) if focused else ''
    active = active_condition or 'true'
    parts.append('''
            <control type="label">
                <width>{width}</width><height>72</height>
                <align>center</align><aligny>center</aligny>
                <font>font13</font><textcolor>{text}</textcolor>
                <label>$INFO[ListItem.Label]</label>
                <visible>{active}{focus_condition}</visible>
            </control>'''.format(width=width, text=TEXT, active=active, focus_condition=focus_condition))
    if active_condition:
        parts.append('''
            <control type="label">
                <width>{width}</width><height>72</height>
                <align>center</align><aligny>center</aligny>
                <font>font13</font><textcolor>{dim}</textcolor>
                <label>$INFO[ListItem.Label]</label>
                <visible>!{active}{focus_condition}</visible>
            </control>
            <control type="image">
                <left>{bar_left}</left><top>62</top><width>50</width><height>4</height>
                <texture colordiffuse="{accent}">mv_white.png</texture>
                <visible>{active}{focus_condition}</visible>
            </control>'''.format(width=width, dim=TEXT_DIM, accent=ACCENT, active=active,
                                 focus_condition=focus_condition, bar_left=(width - 50) // 2))
    return ''.join(parts)


# The backdrop of the preview and the detail page: the glow of the channel colour and the large channel
# logo; a video that keeps playing is shown in its place
BACKDROP_TEMPLATE = '''
            <control type="image">
                <left>{glow_left}</left><top>-330</top><width>1560</width><height>1240</height>
                <fadetime>300</fadetime>
                <texture colordiffuse="{color}">mv_glow.png</texture>
                <visible>!Player.HasVideo + !String.IsEmpty({color_info}) + String.IsEmpty({image_info})</visible>
            </control>
            <control type="image">
                <left>1140</left><top>150</top><width>640</width><height>260</height>
                <aspectratio>keep</aspectratio>
                <fadetime>300</fadetime>
                <texture background="true" colordiffuse="38FFFFFF">{logo}</texture>
                <visible>!Player.HasVideo + String.IsEmpty({image_info})</visible>
            </control>
            <control type="image">
                <left>{video_left}</left><top>0</top><width>{video_width}</width><height>{video_height}</height>
                <aspectratio>scale</aspectratio>
                <fadetime>300</fadetime>
                <texture background="true">{image}</texture>
                <visible>!Player.HasVideo + !String.IsEmpty({image_info})</visible>
            </control>
            <control type="videowindow">
                <left>{video_left}</left><top>0</top><width>{video_width}</width><height>{video_height}</height>
                <visible>Player.HasVideo</visible>
            </control>
            <control type="group">
                <visible>Player.HasVideo | !String.IsEmpty({image_info})</visible>
                <control type="image">
                    <left>{video_left}</left><top>0</top><width>{gradient_width}</width><height>{video_height}</height>
                    <texture>mv_gradient_left.png</texture>
                </control>
                <control type="image">
                    <left>{video_left}</left><top>{bottom_top}</top><width>{video_width}</width><height>{bottom_height}</height>
                    <texture>mv_gradient_bottom.png</texture>
                </control>
            </control>
            <control type="image">
                <width>1920</width><height>220</height>
                <texture flipy="true">mv_gradient_bottom.png</texture>
            </control>'''


def backdrop(color_info, logo_info, image_info, detail=False):
    """color_info, logo_info and image_info are info labels, e.g. ListItem.Property(color)"""
    labels = dict(color='$INFO[%s]' % color_info, color_info=color_info, logo='$INFO[%s]' % logo_info,
                  image='$INFO[%s]' % image_info, image_info=image_info)
    if detail:
        return BACKDROP_TEMPLATE.format(glow_left=500, video_left=420, video_width=1500, video_height=844,
                                        gradient_width=900, bottom_top=480, bottom_height=364, **labels)
    return BACKDROP_TEMPLATE.format(glow_left=560, video_left=560, video_width=1360, video_height=765,
                                    gradient_width=800, bottom_top=400, bottom_height=365, **labels)


PREVIEW_TEMPLATE = '''        <!-- Preview of the focused tile on tabs and channel pages -->
        <control type="group">
            <visible>{visible}</visible>{backdrop}
            <control type="group">
                <left>80</left><top>150</top>
                <visible>![{text_tiles}]</visible>
                <control type="label">
                    <width>1100</width><height>40</height>
                    <font>font14</font>
                    <textcolor>{message}</textcolor>
                    <label>$INFO[{li}ListItem.Property(tile_top)]</label>
                </control>
                <control type="textbox">
                    <top>40</top><width>1100</width><height>132</height>
                    <font>font45_title</font>
                    <textcolor>{text}</textcolor>
                    <label>$INFO[{li}ListItem.Label]</label>
                </control>
                <control type="label">
                    <top>182</top><width>1100</width><height>36</height>
                    <font>font12</font>
                    <textcolor>{text}</textcolor>
                    <label>$INFO[{li}ListItem.Property(meta)]</label>
                </control>
                <control type="textbox">
                    <top>228</top><width>900</width><height>132</height>
                    <font>font12</font>
                    <textcolor>{dim}</textcolor>
                    <label>$INFO[{li}ListItem.Plot]</label>
                    <autoscroll delay="6000" time="3000" repeat="10000">true</autoscroll>
                </control>
            </control>
        </control>

'''


WINDOW_TEMPLATE = '''<?xml version="1.0" encoding="UTF-8"?>
<!-- Generated by .scripts/generate_home_window.py - do not edit manually -->
<window>
    <defaultcontrol>100</defaultcontrol>
    <backgroundcolor>{background}</backgroundcolor>
    <animation effect="fade" time="250">WindowOpen</animation>
    <animation effect="fade" time="200">WindowClose</animation>
    <controls>
        <!--
            A video that keeps playing in the background has to be rendered, otherwise it runs
            out of sync with the audio. It is shown in the preview area of the tabs and of the
            detail page, or dimmed behind the grid.
        -->
        <control type="videowindow">
            <width>1920</width><height>1080</height>
            <visible>Player.HasVideo + {grid_view}</visible>
        </control>
        <control type="image">
            <width>1920</width><height>1080</height>
            <texture colordiffuse="{background}">mv_white.png</texture>
            <visible>!Player.HasVideo | !{grid_view}</visible>
        </control>
        <control type="image">
            <width>1920</width><height>1080</height>
            <texture colordiffuse="E6{background_rgb}">mv_white.png</texture>
            <visible>Player.HasVideo + {grid_view}</visible>
        </control>

{preview}
        <!-- Detail page of a film -->
        <control type="group">
            <visible>String.IsEqual(Window.Property(view),detail)</visible>{detail_backdrop}
            <control type="group">
                <left>80</left><top>130</top>
                <control type="label">
                    <width>1100</width><height>40</height>
                    <font>font14</font>
                    <textcolor>{message}</textcolor>
                    <label>$INFO[Window.Property(detail_channel)]$INFO[Window.Property(detail_show),  ·  ]</label>
                </control>
                <control type="group">
                    <visible>!Control.HasFocus({episode_list})</visible>
                    <control type="textbox">
                        <top>40</top><width>1150</width><height>132</height>
                        <font>font45_title</font>
                        <textcolor>{text}</textcolor>
                        <label>$INFO[Window.Property(detail_title)]</label>
                    </control>
                    <control type="label">
                        <top>182</top><width>1150</width><height>36</height>
                        <font>font12</font>
                        <textcolor>{text}</textcolor>
                        <label>$INFO[Window.Property(detail_meta)]</label>
                    </control>
                    <control type="textbox">
                        <top>228</top><width>900</width><height>132</height>
                        <font>font12</font>
                        <textcolor>{dim}</textcolor>
                        <label>$INFO[Window.Property(detail_plot)]</label>
                        <autoscroll delay="6000" time="3000" repeat="10000">true</autoscroll>
                    </control>
                </control>
                <control type="group">
                    <visible>Control.HasFocus({episode_list})</visible>
                    <control type="textbox">
                        <top>40</top><width>1150</width><height>132</height>
                        <font>font45_title</font>
                        <textcolor>{text}</textcolor>
                        <label>$INFO[Container({episode_list}).ListItem.Label]</label>
                    </control>
                    <control type="label">
                        <top>182</top><width>1150</width><height>36</height>
                        <font>font12</font>
                        <textcolor>{text}</textcolor>
                        <label>$INFO[Container({episode_list}).ListItem.Property(meta)]</label>
                    </control>
                    <control type="textbox">
                        <top>228</top><width>900</width><height>132</height>
                        <font>font12</font>
                        <textcolor>{dim}</textcolor>
                        <label>$INFO[Container({episode_list}).ListItem.Plot]</label>
                        <autoscroll delay="6000" time="3000" repeat="10000">true</autoscroll>
                    </control>
                </control>
            </control>
            <control type="list" id="{button_list}">
                <left>68</left><top>540</top><width>1800</width><height>72</height>
                <orientation>horizontal</orientation>
                <onup>{button_list}</onup>
                <ondown>{button_list}</ondown>
                <scrolltime>150</scrolltime>
                <itemlayout width="{button_width}" height="72">{button_item}
                </itemlayout>
                <focusedlayout width="{button_width}" height="72">{button_focused}
                </focusedlayout>
            </control>
            <control type="group">
                <visible>String.IsEqual(Window.Property(has_episodes),true)</visible>
                <control type="label">
                    <left>80</left><top>652</top><width>1600</width><height>46</height>
                    <font>font14</font>
                    <textcolor>{text}</textcolor>
                    <label>$INFO[Window.Property(detail_episodes_title)]</label>
                </control>
                <control type="list" id="{episode_list}">
                    <left>68</left><top>698</top><width>1852</width><height>{tile_height}</height>
                    <orientation>horizontal</orientation>
                    <onup>{episode_list}</onup>
                    <ondown>{episode_list}</ondown>
                    <scrolltime tween="sine">200</scrolltime>
                    <itemlayout width="{tile_width}" height="{tile_height}">{episode_item}
                    </itemlayout>
                    <focusedlayout width="{tile_width}" height="{tile_height}">{episode_focused}
                    </focusedlayout>
                </control>
            </control>
        </control>

        <!-- Header -->
        <control type="label">
            <left>60</left><top>30</top><width>280</width><height>72</height>
            <aligny>center</aligny>
            <font>font30_title</font>
            <textcolor>{text}</textcolor>
            <label>[B]Mediathek[/B][COLOR {accent}][B]View[/B][/COLOR]</label>
        </control>
        <control type="list" id="100">
            <left>350</left><top>28</top><width>1300</width><height>72</height>
            <orientation>horizontal</orientation>
            <ondown>100</ondown>
            <scrolltime>150</scrolltime>
            <itemlayout width="{nav_width}" height="72">{nav_item}
            </itemlayout>
            <focusedlayout width="{nav_width}" height="72">{nav_focused}
            </focusedlayout>
        </control>
        <control type="label">
            <right>60</right><top>40</top><width>200</width><height>48</height>
            <align>right</align><aligny>center</aligny>
            <font>font14</font>
            <textcolor>{text}</textcolor>
            <label>$INFO[System.Time]</label>
        </control>
        <control type="label">
            <left>80</left><top>104</top><width>1760</width><height>50</height>
            <font>font30_title</font>
            <textcolor>{text}</textcolor>
            <label>$INFO[Window.Property(page_title)]</label>
            <visible>!String.IsEqual(Window.Property(view),detail)</visible>
        </control>

        <!-- Rows -->
        <control type="grouplist" id="300">
            <left>0</left><top>590</top><width>1920</width><height>490</height>
            <orientation>vertical</orientation>
            <itemgap>6</itemgap>
            <scrolltime tween="sine">250</scrolltime>
            <onup>100</onup>
            <visible>String.IsEqual(Window.Property(view),tab)</visible>
            {rows}
        </control>

        <!-- Grid for "show all", shows, search results and the following pages -->
        <control type="group">
            <visible>String.IsEqual(Window.Property(view),grid)</visible>
            <control type="panel" id="600">
                <left>68</left><top>180</top><width>1820</width><height>900</height>
                <orientation>vertical</orientation>
                <onup>100</onup>
                <scrolltime tween="sine">250</scrolltime>
                <itemlayout width="{grid_width}" height="{grid_height}">{grid_item}
                </itemlayout>
                <focusedlayout width="{grid_width}" height="{grid_height}">{grid_focused}
                </focusedlayout>
            </control>
        </control>

        <!-- Status -->
        <control type="label">
            <left>80</left><bottom>30</bottom><width>1760</width><height>40</height>
            <align>center</align>
            <font>font13</font>
            <textcolor>{dim}</textcolor>
            <label>{loading}</label>
            <visible>String.IsEqual(Window.Property(loading),true)</visible>
            <animation effect="fade" start="100" end="40" time="800" pulse="true" condition="true">Conditional</animation>
        </control>
        <control type="label">
            <left>80</left><top>700</top><width>1760</width><height>60</height>
            <align>center</align>
            <font>font14</font>
            <textcolor>{dim}</textcolor>
            <label>{empty}</label>
            <visible>String.IsEqual(Window.Property(empty),true) + !String.IsEqual(Window.Property(loading),true) + String.IsEmpty(Window.Property(empty_text))</visible>
        </control>
        <control type="textbox">
            <left>360</left><top>660</top><width>1200</width><height>120</height>
            <align>center</align>
            <font>font14</font>
            <textcolor>{dim}</textcolor>
            <label>$INFO[Window.Property(empty_text)]</label>
            <visible>String.IsEqual(Window.Property(empty),true) + !String.IsEmpty(Window.Property(empty_text))</visible>
        </control>
    </controls>
</window>
'''


def preview():
    """Preview of the focused tile, or of the first tile of the first row while the tab bar has the focus"""
    tab = 'String.IsEqual(Window.Property(view),tab)'
    parts = []
    for prefix, visible in (('', tab + ' + !Control.HasFocus(100)'), ('Container(301).', tab + ' + Control.HasFocus(100)')):
        parts.append(PREVIEW_TEMPLATE.format(
            visible=visible, li=prefix, text=TEXT, dim=TEXT_DIM, message=MESSAGE,
            text_tiles=is_kind(*TEXT_KINDS, prefix=prefix)[1:-1],
            backdrop=backdrop('%sListItem.Property(color)' % prefix, '%sListItem.Property(logo)' % prefix,
                              '%sListItem.Property(image)' % prefix)))
    return ''.join(parts)


def generate_window():
    xml = WINDOW_TEMPLATE.format(
        preview=preview(),
        detail_backdrop=backdrop('Window.Property(detail_color)', 'Window.Property(detail_logo)',
                                 'Window.Property(detail_image)', detail=True),
        background=BACKGROUND,
        background_rgb=BACKGROUND[2:],
        grid_view='String.IsEqual(Window.Property(view),grid)',
        text=TEXT, dim=TEXT_DIM, message=MESSAGE, accent=ACCENT,
        nav_width=NAV_WIDTH,
        nav_item=pill_layout(100, False, NAV_WIDTH, 'String.IsEqual(ListItem.Property(active),true)'),
        nav_focused=pill_layout(100, True, NAV_WIDTH, 'String.IsEqual(ListItem.Property(active),true)'),
        button_list=700, episode_list=720,
        button_width=360,
        button_item=pill_layout(700, False, 360),
        button_focused=pill_layout(700, True, 360),
        tile_width=TILE_WIDTH, tile_height=TILE_HEIGHT,
        episode_item=tile_layout(720, False, TILE_WIDTH, THUMB_WIDTH, THUMB_HEIGHT, 'mv_shade.png'),
        episode_focused=tile_layout(720, True, TILE_WIDTH, THUMB_WIDTH, THUMB_HEIGHT, 'mv_shade.png'),
        rows=''.join(row(index) for index in range(NUM_ROWS)),
        grid_width=GRID_TILE_WIDTH, grid_height=GRID_TILE_HEIGHT,
        grid_item=tile_layout(600, False, GRID_TILE_WIDTH, GRID_THUMB_WIDTH, GRID_THUMB_HEIGHT, 'mv_shade_grid.png'),
        grid_focused=tile_layout(600, True, GRID_TILE_WIDTH, GRID_THUMB_WIDTH, GRID_THUMB_HEIGHT, 'mv_shade_grid.png'),
        loading=localize(30675),
        empty=localize(30676),
    )
    directory = os.path.dirname(XML_PATH)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(XML_PATH, 'w', encoding='utf-8') as xml_file:
        xml_file.write(xml)


if __name__ == '__main__':
    generate_textures()
    generate_logos()
    generate_window()
    print('Generated', os.path.relpath(XML_PATH, ROOT))
