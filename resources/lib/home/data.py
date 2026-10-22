# -*- coding: utf-8 -*-
"""
Data of the TV interface: films, shows, channels and livestreams from the
database as items for the tiles of the home window, and the playback
positions for "Continue watching".

SPDX-License-Identifier: MIT
"""
import fnmatch
import json
import os
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta

import resources.lib.appContext as appContext
import resources.lib.mvutils as mvutils
import resources.lib.extendedSearchModel as ExtendedSearchModel
from resources.lib.home import fontwidths

try:
    from resources.lib import images
except ImportError:
    # Python without lzma
    images = None

KIND_FILM = 'film'
KIND_LIVE = 'live'
KIND_CHANNEL = 'channel'
KIND_SHOW = 'show'
KIND_LETTER = 'letter'
KIND_ACTION = 'action'
KIND_ALL = 'all'
KIND_NEXT = 'next'
KIND_BACK = 'back'

ROW_MAX_ITEMS = 30
SEARCH_MAX_ITEMS = 500
SHOW_MAX_ITEMS = 500
# rows of the home tab skip short clips like trailers and weather reports
HOME_MIN_DURATION = 300
HISTORY_MAX_ITEMS = 60

# titles of the film tiles: (font size, width) in pixels of the skin; Kodi cannot tell how many
# lines a text takes, the titles are wrapped here and the skin places them by their number of lines
TITLE_ROW = (30, 348)
TITLE_GRID = (25, 304)
TITLE_MAX_LINES = 3
# Kodi draws Noto Sans about 3.6 % narrower than its nominal widths (measured on screenshots)
TEXT_SCALE = 0.965

# Colour (RRGGBB) and logo of the channels, the logo files are generated from
# resources/icons by .scripts/generate_home_window.py
CHANNELS = {
    'ard': ('ARD', '1D4C9E', 'ard'),
    'zdf': ('ZDF', 'E8711A', 'zdf'),
    '3sat': ('3sat', 'B0234D', '3sat'),
    'arte.de': ('arte', 'D9481F', 'arte.de'),
    'arte.fr': ('arte (FR)', 'D9481F', 'arte.de'),
    'arte.en': ('arte (EN)', 'D9481F', 'arte.de'),
    'arte.es': ('arte (ES)', 'D9481F', 'arte.de'),
    'arte.it': ('arte (IT)', 'D9481F', 'arte.de'),
    'arte.pl': ('arte (PL)', 'D9481F', 'arte.de'),
    'br': ('BR', '0A6DB0', 'br'),
    'hr': ('hr', '005CA9', 'hr'),
    'mdr': ('MDR', '0C5596', 'mdr'),
    'ndr': ('NDR', '163F86', 'ndr'),
    'rbb': ('rbb', 'B5172F', 'rbb'),
    'sr': ('SR', '0067A6', 'sr'),
    'swr': ('SWR', '3A4654', 'swr'),
    'wdr': ('WDR', '0B3B63', 'wdr'),
    'kika': ('KiKA', '5E9E1B', 'kika'),
    'phoenix': ('phoenix', '007FB8', 'phoenix'),
    'one': ('ONE', '74237F', 'one'),
    'ard-alpha': ('ARD alpha', '00809F', 'ard-alpha'),
    'tagesschau24': ('tagesschau24', '0E2E5A', 'tagesschau24'),
    'zdfinfo': ('ZDFinfo', '2F6584', 'zdf.info'),
    'zdfneo': ('ZDFneo', '177C7C', 'zdf.neo'),
    'zdf-tivi': ('ZDFtivi', 'DE7E00', 'zdf-tivi'),
    'dw': ('DW', '0A62A0', 'dw'),
    'funk.net': ('funk', 'D9540B', 'funk.net'),
    'orf': ('ORF', 'B3171D', 'orf'),
    'srf': ('SRF', 'A3122A', 'srf'),
    'srf.podcast': ('SRF Podcast', 'A3122A', 'srf'),
    'rbtv': ('Radio Bremen', 'C8102E', 'rbtv'),
    'radio bremen tv': ('Radio Bremen', 'C8102E', 'rbtv'),
}
DEFAULT_COLOR = '2A3B55'
# arte in other languages is left out of the rows of the home tab
FOREIGN_CHANNELS = ('ARTE.FR', 'ARTE.EN', 'ARTE.ES', 'ARTE.IT', 'ARTE.PL')

# logos of livestreams of regional or additional programmes (name without blanks)
LIVESTREAM_LOGOS = {
    'ARDAlphaLivestream': 'ardalpha',
    'ARDONELivestream': 'one',
    'ARDTagesschauLivestream': 'tagesschau24',
    'MDRSachsenLivestream': 'mdr-s',
    'MDRSachsen-AnhaltLivestream': 'mdr-sa',
    'MDRThüringenLivestream': 'mdr-th',
    'ORF-1Livestream': 'orf1',
    'ORF-2Livestream': 'orf2',
    'ORF-3Livestream': 'orf3',
    'ORF-SportLivestream': 'orfsport',
    'ZDF.infoLivestream': 'zdf.info',
    'ZDF.neoLivestream': 'zdf.neo',
}

# shows that collect feature films (patterns of LIKE)
FILM_SHOWS = ('Filme%', 'Kino - Filme%', '%Fernsehfilm%', 'Das kleine Fernsehspiel', 'Spielfilm%')
# shows of unrelated films: a film without an image does not get the image of another one
COLLECTION_SHOWS = FILM_SHOWS + ('Dokumentation%', '%Dokumentarfilm%', '%Kurzfilm%')

# rows of the home tab built from show names: (string id, conditions on showname, minimum duration, days)
GENRE_ROWS = (
    (30623, tuple("showname LIKE '%s'" % pattern for pattern in FILM_SHOWS), 3600, 30),
    (30625, ("showname = 'Tatort'", "showname LIKE 'Polizeiruf%'", "showname LIKE '%Krimi%'",
             "showname LIKE 'SOKO%'", "showname = 'Der Alte'", "showname = 'Ein starkes Team'"), 2400, 30),
    (30624, ("showname LIKE '%Doku%'", "showname LIKE 'Dokumentation%'", "showname LIKE '%Reportage%'",
             "showname = 'Terra X'", "showname LIKE '%Dokumentarfilm%'"), 1500, 30),
)


def _settings():
    return appContext.MVSETTINGS


def _language(string_id):
    return appContext.ADDONCLASS.getLocalizedString(string_id)


def addon_path():
    return mvutils.py2_decode(appContext.ADDONCLASS.getAddonInfo('path'))


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class Database(object):
    """ A connection for one task of the window, SQLite connections cannot be shared by threads """

    def __enter__(self):
        if _settings().getDatabaseType() == 1:
            from resources.lib.storeMySql import StoreMySQL
            self.store = StoreMySQL()
        else:
            from resources.lib.storeSqlite import StoreSQLite
            self.store = StoreSQLite()
        return self

    def __exit__(self, *args):
        try:
            self.store.exit()
        # pylint: disable=broad-except
        except Exception:
            pass

    def execute(self, sql, params=()):
        return self.store.execute(sql, list(params))

    def films(self, conditions=(), params=(), order='aired DESC', limit=ROW_MAX_ITEMS, home=False):
        """ Films matching the conditions and the filters of the settings """
        settings = _settings()
        where = list(conditions)
        args = list(params)
        where.append("showname <> 'LIVESTREAM'")
        if settings.getNoFutur():
            # '+aired': the index does not help for a condition that matches nearly all films
            where.append('+aired < ?')
            args.append(int(time.time()))
        min_length = settings.getMinLength() * 60
        if home:
            min_length = max(min_length, HOME_MIN_DURATION)
        if min_length > 0:
            where.append('duration >= ?')
            args.append(min_length)
        for word in (settings.getBlacklist() or '').split('|'):
            if word.strip():
                where.append('title NOT LIKE ? AND showname NOT LIKE ?')
                args.extend(['%' + word.strip() + '%'] * 2)
        if hide_variants():
            where.append("title NOT LIKE '%Audiodeskription%' AND title NOT LIKE '%Gebärdensprache%'"
                         " AND showname NOT LIKE '%Gebärdensprache%' AND title NOT LIKE '%Hörfassung%'")
        sql = ('SELECT idhash, title, showname, channel, description, duration, aired, url_sub, url_video, '
               'url_video_sd, url_video_hd, showid FROM film WHERE ' + ' AND '.join('(%s)' % c for c in where))
        sql += ' ORDER BY ' + order
        if limit:
            sql += ' LIMIT %d' % int(limit)
        return [film_item(row) for row in self.execute(sql, args)]

    def status(self):
        rows = self.execute('SELECT status, lastupdate FROM status')
        return rows[0] if rows else (None, 0)

    def since(self, days):
        """
        Start of the last days for "new" films: counted from the date of the film list, a database
        that was not updated for a while would show empty rows otherwise
        """
        if not hasattr(self, '_listed'):
            try:
                rows = self.execute('SELECT filmupdate, lastupdate FROM status')
                self._listed = max(rows[0]) if rows else 0
            # pylint: disable=broad-except
            except Exception:
                self._listed = 0
        now = int(time.time())
        reference = min(now, int(self._listed or 0)) or now
        return reference - days * 86400


def hide_variants():
    return appContext.ADDONCLASS.getSetting('tvhidevariants') != 'false'


def _dedupe(items, limit):
    """ The same film is often listed twice, e.g. by ARD and the regional channel """
    seen = set()
    result = []
    for item in items:
        key = (item['label'].lower(), item['show'].lower(), item['aired'] // 600)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def recent_films(db, channels=None, days=2, limit=ROW_MAX_ITEMS, home=True, exclude=None):
    conditions = ['aired > ?']
    params = [db.since(days)]
    if channels:
        conditions.append('channel IN (%s)' % ','.join('?' * len(channels)))
        params.extend(channels)
    if exclude:
        conditions.append('channel NOT IN (%s)' % ','.join('?' * len(exclude)))
        params.extend(exclude)
    return _dedupe(db.films(conditions, params, limit=limit * 2, home=home), limit)


def genre_films(db, show_conditions, min_duration, days, limit=ROW_MAX_ITEMS):
    conditions = ['(%s)' % ' OR '.join(show_conditions), 'duration >= ?', 'aired > ?',
                  'channel NOT IN (%s)' % ','.join('?' * len(FOREIGN_CHANNELS))]
    params = [min_duration, db.since(days)] + list(FOREIGN_CHANNELS)
    return _dedupe(db.films(conditions, params, limit=limit * 2, home=True), limit)


def show_films(db, showids, channels=None, limit=SHOW_MAX_ITEMS):
    conditions = ['showid IN (%s)' % ','.join('?' * len(showids))]
    params = list(showids)
    if channels:
        conditions.append('channel IN (%s)' % ','.join('?' * len(channels)))
        params.extend(channels)
    return db.films(conditions, params, limit=limit)


def search_films(db, query, channel=None, limit=SEARCH_MAX_ITEMS):
    pattern = '%' + query + '%'
    conditions, params = ['showname LIKE ? OR title LIKE ?'], [pattern, pattern]
    if channel:
        conditions.append('channel = ?')
        params.append(channel)
    return db.films(conditions, params, limit=limit)


def saved_search_films(db, entry, limit=SEARCH_MAX_ITEMS):
    """ Films of a search saved in the extended search of the classic menu """
    model = ExtendedSearchModel.ExtendedSearchModel('').fromDict(entry)
    rows = db.store.extendedSearchQuery(model) or []
    return [film_item(tuple(row) + ('',)) for row in rows[:limit]]


def film_by_id(db, filmid):
    rows = db.execute('SELECT idhash, title, showname, channel, description, duration, aired, url_sub, url_video, '
                      'url_video_sd, url_video_hd, showid FROM film WHERE idhash = ?', [filmid])
    return film_item(rows[0]) if rows else None


def livestreams(db):
    # sorted here: sorting by channel would make the database read every film through the channel index
    rows = db.execute("SELECT idhash, title, channel, url_video FROM film WHERE showname = 'LIVESTREAM'")
    return [live_item(row) for row in sorted(rows, key=lambda r: ((r[2] or '').lower(), (r[1] or '').lower()))]


def channels(db):
    # counted with the index alone, the few livestreams do not matter
    rows = db.execute('SELECT channel, COUNT(*) FROM film GROUP BY channel')
    items = [channel_item(channel, count) for channel, count in rows]
    # German channels first, ordered like on the remote control, then the others
    order = ['ard', 'zdf', '3sat', 'arte.de', 'phoenix', 'kika', 'zdf-tivi', 'one', 'zdfneo', 'zdfinfo', 'tagesschau24',
             'ard-alpha', 'br', 'hr', 'mdr', 'ndr', 'rbb', 'sr', 'swr', 'wdr', 'rbtv', 'radio bremen tv', 'funk.net', 'dw']
    items.sort(key=lambda item: (order.index(item['channel'].lower()) if item['channel'].lower() in order else len(order),
                                 item['label'].lower()))
    return items


def channel_shows(db, channel, recent_days=None, limit=None):
    """ Shows of a channel, by name or the ones with the newest episodes first """
    sql = 'SELECT showid, showname, COUNT(*), MAX(aired) FROM film WHERE channel = ?'
    params = [channel]
    if recent_days:
        sql += ' AND aired > ?'
        params.append(db.since(recent_days))
    if _settings().getNoFutur():
        sql += ' AND +aired < ?'
        params.append(int(time.time()))
    sql += " AND showname <> 'LIVESTREAM' GROUP BY showid, showname"
    sql += ' ORDER BY MAX(aired) DESC' if recent_days else ' ORDER BY showname'
    if limit:
        sql += ' LIMIT %d' % int(limit)
    return [show_item([showid], name, [channel], count, latest) for showid, name, count, latest in db.execute(sql, params)
            if not (hide_variants() and 'Gebärdensprache' in (name or ''))]


LETTER_CASE = ("CASE WHEN UPPER(SUBSTR(showname,1,1)) BETWEEN 'A' AND 'Z' THEN UPPER(SUBSTR(showname,1,1)) "
               "WHEN SUBSTR(showname,1,1) BETWEEN '0' AND '9' THEN '0' ELSE '#' END")


def letters(db, channel=None):
    """ Start letters of the shows, of all channels or of one """
    where, params = "showname <> 'LIVESTREAM'", []
    if channel:
        where += ' AND channel = ?'
        params.append(channel)
    rows = db.execute('SELECT %s AS letter, COUNT(DISTINCT showname) FROM film WHERE %s GROUP BY %s ORDER BY %s'
                      % (LETTER_CASE, where, LETTER_CASE, LETTER_CASE), params)
    color = channel_info(channel)[1] if channel else ''
    return [make_item(KIND_LETTER, letter, letter=letter, channel=channel or '', channels=[channel] if channel else [],
                      color=color, subtitle=_language(30684) % format_count(count)) for letter, count in rows]


def letter_shows(db, letter, channel=None):
    """ Shows starting with a letter, a show broadcast by several channels is one tile """
    sql = ('SELECT showname, GROUP_CONCAT(DISTINCT showid), GROUP_CONCAT(DISTINCT channel), COUNT(*), MAX(aired) '
           'FROM film WHERE %s = ? AND showname <> \'LIVESTREAM\'' % LETTER_CASE)
    params = [letter]
    if channel:
        sql += ' AND channel = ?'
        params.append(channel)
    if _settings().getNoFutur():
        sql += ' AND +aired < ?'
        params.append(int(time.time()))
    sql += ' GROUP BY showname ORDER BY showname'
    return [show_item(showids.split(','), name, channel_list.split(','), count, latest)
            for name, showids, channel_list, count, latest in db.execute(sql, params)
            if not (hide_variants() and 'Gebärdensprache' in (name or ''))]


def new_shows(db, days=2, limit=ROW_MAX_ITEMS):
    """ Shows with new episodes, newest first """
    sql = ('SELECT showname, GROUP_CONCAT(DISTINCT showid), GROUP_CONCAT(DISTINCT channel), COUNT(*), MAX(aired) '
           'FROM film WHERE aired > ? AND +aired < ? AND duration >= ? AND showname <> \'LIVESTREAM\' AND channel NOT IN (%s) '
           'GROUP BY showname ORDER BY MAX(aired) DESC LIMIT %d' % (','.join('?' * len(FOREIGN_CHANNELS)), limit * 2))
    params = [db.since(days), int(time.time()), HOME_MIN_DURATION] + list(FOREIGN_CHANNELS)
    items = [show_item(showids.split(','), name, channel_list.split(','), count, latest)
             for name, showids, channel_list, count, latest in db.execute(sql, params)
             if not (hide_variants() and 'Gebärdensprache' in (name or ''))]
    return items[:limit]


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def make_item(kind, label, **kwargs):
    item = {
        'kind': kind, 'label': label, 'subtitle': '', 'plot': '', 'channel': '', 'color': '', 'logo': '', 'tile_top': '',
        'meta': '', 'message': '', 'filmid': '', 'show': '', 'showids': [], 'channels': [], 'duration': 0, 'aired': 0,
        'url': '', 'url_sd': '', 'url_hd': '', 'url_sub': '', 'progress': 0, 'resume': 0, 'action': '', 'letter': '',
        'more': None, 'row_title': '', 'entry': None, 'key': 0, 'image': '',
    }
    item.update(kwargs)
    return item


def text_width(text, size):
    widths = fontwidths.WIDTHS
    return sum(widths.get(ord(char), fontwidths.DEFAULT) for char in text) * size * TEXT_SCALE / 1000.0


def wrap_title(text, font, max_lines=TITLE_MAX_LINES):
    """
    Lines of a title on a tile: wrapped at spaces like Kodi does, a word wider than the
    tile is hyphenated, a title longer than max_lines ends with an ellipsis
    """
    size, width = font
    lines = []
    line = ''
    for word in (text or '').split():
        candidate = line + ' ' + word if line else word
        if text_width(candidate, size) <= width:
            line = candidate
            continue
        if line:
            lines.append(line)
        line = word
        while text_width(line, size) > width:
            # at least three letters on the next line
            cut = len(line) - 3
            while cut > 3 and text_width(line[:cut] + '-', size) > width:
                cut -= 1
            lines.append(line[:cut] + '-')
            line = line[cut:]
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        last = lines[max_lines - 1]
        while ' ' in last and text_width(last + '\u2026', size) > width:
            last = last.rsplit(' ', 1)[0]
        while last and text_width(last + '\u2026', size) > width:
            last = last[:-1]
        lines = lines[:max_lines - 1] + [last.rstrip(' -,.:;\u2013') + '\u2026']
    return lines


def channel_info(channel):
    """ (name, colour AARRGGBB, logo path) of a channel """
    name, color, logo = CHANNELS.get((channel or '').lower(), (channel or '', DEFAULT_COLOR, ''))
    return name, 'FF' + color, logo_path(logo)


def logo_path(name):
    if not name:
        return ''
    path = os.path.join(addon_path(), 'resources', 'skins', 'default', 'media', 'logos', name + '.png')
    return path if mvutils.file_exists(path) else ''


def small_logo(path):
    """ The logo in the size of the tiles """
    if not path:
        return ''
    small = os.path.join(os.path.dirname(path), 'small', os.path.basename(path))
    return small if mvutils.file_exists(small) else path


def format_duration(seconds):
    if not seconds:
        return ''
    # minutes like in the media libraries: "89 Min."
    return _language(30679) % max(int(round(seconds / 60.0)), 1)


def format_date(timestamp, with_time=True):
    """ "Today, 20:15", "Yesterday, 20:15" or "Mon, 20.10.2026, 20:15" in local time """
    aired = mvutils.local_datetime(timestamp)
    if aired is None:
        return ''
    today = datetime.now().date()
    if aired.date() == today:
        day = _language(30677)
    elif aired.date() == today - timedelta(days=1):
        day = _language(30678)
    else:
        weekday = _language(30690 + aired.weekday())
        day = '%s, %s' % (weekday, aired.strftime('%d.%m.' if aired.year == today.year else '%d.%m.%Y'))
    if with_time:
        return '%s, %s' % (day, aired.strftime('%H:%M'))
    return day


def clean_title(title, show):
    """ ARD appends the show to the title: "Episode | Show" """
    if ' | ' in title and show:
        head, _, tail = title.rpartition(' | ')
        if tail.strip().lower().replace('–', '-') == show.strip().lower().replace('–', '-'):
            return head.strip()
    return title


def film_item(row):
    (filmid, title, show, channel, description, duration, aired, url_sub, url_video, url_sd, url_hd, showid) = row[:12]
    name, color, logo = channel_info(channel)
    title = title or ''
    show = show or ''
    label = clean_title(title, show) or show
    aired = int(aired or 0)
    duration = int(duration or 0)
    meta = [name, format_date(aired), format_duration(duration)]
    flags = []
    if url_hd:
        flags.append('HD')
    if url_sub:
        flags.append(_language(30680))
    return make_item(
        KIND_FILM, label, filmid=filmid, key=images.video_key(url_video) if images else 0, show=show, showids=[showid] if showid else [], channel=channel or '',
        channels=[channel] if channel else [], color=color, logo=logo, tile_top=show if show.lower() != label.lower() else '',
        subtitle='  ·  '.join(x for x in (format_date(aired), format_duration(duration)) if x),
        meta='  ·  '.join(x for x in meta + flags if x), plot=description or '', duration=duration, aired=aired,
        url=url_video or '', url_sd=url_sd or '', url_hd=url_hd or '', url_sub=url_sub or '')


def live_item(row):
    (filmid, title, channel, url) = row
    name, color, logo = channel_info(channel)
    own_logo = LIVESTREAM_LOGOS.get((title or '').replace(' ', ''))
    if own_logo:
        logo = logo_path(own_logo) or logo
    label = (title or '').replace(' Livestream', '').replace('Livestream', '').strip() or name
    # "ARTE.FR" -> "arte (FR)", "ORF-1" -> "ORF 1", "3Sat" -> "3sat"
    if label.lower() == (channel or '').lower():
        label = name
    elif label.lower().startswith('arte.'):
        label = channel_info(label.split()[0])[0]
    label = label.replace('ORF-', 'ORF ').replace('3Sat', '3sat').replace('ARD Alpha', 'ARD alpha').replace('ZDF.neo', 'ZDFneo').replace('ZDF.info', 'ZDFinfo')
    return make_item(KIND_LIVE, label, filmid=filmid, channel=channel, channels=[channel], color=color, logo=logo,
                     subtitle=_language(30685), meta=_language(30622), url=url or '',
                     tile_top=name if name.lower() != label.lower() else '')


def format_count(count):
    """ 150.662 """
    return '{:,}'.format(int(count or 0)).replace(',', '.')


def channel_item(channel, count):
    name, color, logo = channel_info(channel)
    videos = _language(30686) % format_count(count)
    return make_item(KIND_CHANNEL, name, channel=channel, channels=[channel], color=color, logo=logo,
                     subtitle='%s  ·  %s' % (name, videos), meta=videos)


def show_item(showids, name, channel_list, count, latest):
    channel_list = [c for c in channel_list if c]
    first = channel_list[0] if channel_list else ''
    channel_name, color, logo = channel_info(first)
    names = ', '.join(channel_info(c)[0] for c in channel_list)
    if len(channel_list) > 1:
        logo = ''
        color = 'FF' + DEFAULT_COLOR
    latest_text = _language(30687) % format_date(latest, with_time=False) if latest else ''
    return make_item(KIND_SHOW, name or '', show=name or '', showids=list(showids), channel=first, channels=channel_list,
                     color=color, logo=logo, tile_top=names, subtitle=_language(30686) % format_count(count),
                     meta='  ·  '.join(x for x in (names, _language(30686) % format_count(count), latest_text) if x))


# ---------------------------------------------------------------------------
# Preview images from the websites of the media libraries
# ---------------------------------------------------------------------------

def images_enabled():
    return images is not None and appContext.ADDONCLASS.getSetting('tvimages') != 'false'


def image_store():
    return images.ImageStore(_settings().getDatapath())


def _collection(show):
    name = (show or '').lower()
    return any(fnmatch.fnmatchcase(name, pattern.lower().replace('%', '*')) for pattern in COLLECTION_SHOWS)


def attach_images(items):
    """ The images that are known already, returns the films and shows that still need one """
    if not images_enabled():
        return []
    candidates = [i for i in items if i['kind'] in (KIND_FILM, KIND_SHOW) and not i['image']]
    if not candidates:
        return []
    known, shows = image_store().cached([i['key'] for i in candidates],
                                        [s for i in candidates for s in i['showids']])
    missing = []
    for item in candidates:
        own = known.get(item['key'])
        show = next((shows[s] for s in item['showids'] if s in shows), '')
        if item['kind'] == KIND_FILM and _collection(item['show']):
            show = ''
        item['image'] = own or show
        # a film with the image of its show still looks for its own
        if not own and (item['kind'] == KIND_FILM or not show):
            missing.append(item)
    return missing


def resolve_image(item):
    """ Image of a film or show from the website, '' if there is none; runs in the background """
    store = image_store()
    showid = item['showids'][0] if item['showids'] else ''
    if item['kind'] == KIND_FILM:
        image = store.resolve(item['key'], showid) if item['key'] else ''
        if image or _collection(item['show']):
            return image
        return next(iter(store.cached([], item['showids'])[1].values()), '')
    # a show: the image of its newest episode that has one
    with Database() as db:
        rows = db.execute('SELECT url_video FROM film WHERE showid IN (%s) ORDER BY aired DESC LIMIT 3'
                          % ','.join('?' * len(item['showids'])), item['showids']) if item['showids'] else []
    for (url,) in rows:
        image = store.resolve(images.video_key(url), showid)
        if image:
            return image
    return ''


# ---------------------------------------------------------------------------
# Livestream groups of the live tab
# ---------------------------------------------------------------------------

LIVE_NATIONWIDE = ('3sat', 'ard', 'arte.de', 'kika', 'phoenix', 'zdf', 'dw')


def live_rows(items):
    nationwide, regional, local, international = [], [], [], []
    for item in items:
        channel = item['channel'].lower()
        if 'Lokalzeit' in item['label']:
            local.append(item)
        elif channel in ('orf', 'arte.fr', 'srf') or item['label'].endswith(('(EN)', '(FR)')):
            international.append(item)
        elif channel in LIVE_NATIONWIDE and not any(x in item['label'] for x in ('Nord', 'Süd')):
            nationwide.append(item)
        else:
            regional.append(item)
    order = ['ARD', 'ZDF', '3sat', 'arte', 'ARD ONE', 'ZDFneo', 'ZDFinfo', 'phoenix', 'KiKA', 'ARD Tagesschau', 'ARD alpha', 'DW']

    def _rank(item):
        for position, prefix in enumerate(order):
            if item['label'].lower() == prefix.lower():
                return position
        return len(order)
    nationwide.sort(key=_rank)
    return [(30630, nationwide), (30631, regional), (30632, local), (30633, international)]


# ---------------------------------------------------------------------------
# Playback positions ("Continue watching") and recent searches
# ---------------------------------------------------------------------------

_HISTORY_LOCK = threading.Lock()


def _profile_file(name):
    return os.path.join(_settings().getDatapath(), name)


def _load_json(name, default):
    path = _profile_file(name)
    if not mvutils.file_exists(path):
        return default
    try:
        with closing(open(path, encoding='utf-8')) as json_file:
            data = json.load(json_file)
        return data if isinstance(data, type(default)) else default
    # pylint: disable=broad-except
    except Exception:
        return default


def _save_json(name, data):
    path = _profile_file(name)
    try:
        with closing(open(path + '.tmp', 'w', encoding='utf-8')) as json_file:
            json.dump(data, json_file)
        mvutils.file_rename(path + '.tmp', path)
    # pylint: disable=broad-except
    except Exception as err:
        appContext.MVLOGGER.get_new_logger('Home').error('Failed to write {}: {}', path, err)


def load_history():
    """ {filmid: {'position', 'total', 'when', 'item'}} """
    with _HISTORY_LOCK:
        return _load_json('tvhistory.json', {})


def save_position(item, position, total):
    """ Remember where playback of a film stopped, finished films are kept with position 0 """
    if not item.get('filmid') or total <= 0:
        return
    finished = position >= total * 0.95 or total - position < 60
    with _HISTORY_LOCK:
        history = _load_json('tvhistory.json', {})
        stored = dict((k, v) for k, v in item.items() if k in ('kind', 'label', 'filmid', 'show', 'showids', 'channel',
                                                                 'duration', 'aired', 'plot', 'url', 'url_sd', 'url_hd', 'url_sub'))
        history[item['filmid']] = {'position': 0 if finished else int(position), 'total': int(total),
                                   'finished': finished, 'when': int(time.time()), 'item': stored}
        if len(history) > HISTORY_MAX_ITEMS:
            for key in sorted(history, key=lambda k: history[k]['when'])[:len(history) - HISTORY_MAX_ITEMS]:
                del history[key]
        _save_json('tvhistory.json', history)


def remove_from_history(filmid):
    with _HISTORY_LOCK:
        history = _load_json('tvhistory.json', {})
        if history.pop(filmid, None) is not None:
            _save_json('tvhistory.json', history)


def apply_history(items, history=None):
    """ Progress and resume position of the films the user started """
    history = load_history() if history is None else history
    for item in items:
        entry = history.get(item.get('filmid')) if item['kind'] == KIND_FILM else None
        if entry:
            item['resume'] = entry['position']
            item['progress'] = 100 if entry.get('finished') else int(entry['position'] * 100 / max(entry['total'], 1))
    return items


def continue_watching(db):
    """ Started films, newest first, as items with the current data of the database """
    history = load_history()
    entries = sorted((e for e in history.values() if not e.get('finished') and e['position'] > 0),
                     key=lambda e: e['when'], reverse=True)
    items = []
    for entry in entries[:ROW_MAX_ITEMS]:
        item = film_by_id(db, entry['item']['filmid'])
        if item is None:
            # no longer in the media library
            continue
        items.append(item)
    return apply_history(items, history)


def load_searches():
    return _load_json('tvsearches.json', [])


def save_search(query):
    searches = [q for q in load_searches() if q.lower() != query.lower()]
    _save_json('tvsearches.json', [query] + searches[:14])


def remove_search(query):
    _save_json('tvsearches.json', [q for q in load_searches() if q != query])


def saved_searches():
    """ Searches of the extended search of the classic menu """
    entries = _load_json('searchConfig.json', [])
    return sorted((e for e in entries if isinstance(e, dict) and 'id' in e), key=lambda e: e.get('when', 0), reverse=True)
