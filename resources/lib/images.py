# -*- coding: utf-8 -*-
"""
Preview images of the films from the websites of the media libraries.

The database of MediathekView has no images and no websites. The film list
(JSON) has the website of every film: the service keeps an index from the
video url to the website (websites.db: the full list once a week, the small
list of changes once an hour). The TV interface looks up the image of a
film on its website when its tile is shown and keeps it (images.db); a film
without an image of its own gets the image of another episode of its show.

SPDX-License-Identifier: MIT
"""
import codecs
import hashlib
import json
import lzma
import os
import re
import sqlite3
import threading
import time

try:
    from html import unescape
    from urllib.parse import quote, urlsplit, urlunsplit
    from urllib.request import Request, urlopen
except ImportError:
    from HTMLParser import HTMLParser
    unescape = HTMLParser().unescape
    from urllib import quote
    from urlparse import urlsplit, urlunsplit
    from urllib2 import Request, urlopen

FILMLISTE_URL = 'https://liste.mediathekview.de/'
FULL_LIST = 'Filmliste-akt.xz'
DIFF_LIST = 'Filmliste-diff.xz'
FULL_INTERVAL = 7 * 86400
DIFF_INTERVAL = 3600
# films without an image are looked up again after a while, the page may have changed
RETRY_SECONDS = 7 * 86400
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Kodi MediathekView'
PAGE_BYTES = 400000
TIMEOUT = 8

_OG_IMAGE = (re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', re.I),
             re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.I))
_ARD_ID = re.compile(r'ardmediathek\.de/video/(?:.*/)?([A-Za-z0-9_=-]{16,})/?$')

_LOCK = threading.Lock()


def video_key(url):
    """ Key of a film: 60 bits of the md5 of its video url """
    if not url:
        return 0
    return int(hashlib.md5(url.encode('utf-8')).hexdigest()[:15], 16)


def _quote_url(url):
    """ Websites can contain umlauts, urllib needs ASCII """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.encode('idna').decode('ascii'),
                       quote(parts.path, safe='/%:@!$&\'()*+,;=-._~'),
                       quote(parts.query, safe='=&%:/?+,;@!$\'()*-._~'), ''))


def _get(url, limit=PAGE_BYTES):
    request = Request(_quote_url(url), headers={'User-Agent': USER_AGENT, 'Accept-Language': 'de-DE,de;q=0.9'})
    response = urlopen(request, timeout=TIMEOUT)
    try:
        return response.read(limit).decode('utf-8', 'replace')
    finally:
        response.close()


def _sized(image):
    """ Images about 960 pixels wide instead of full HD, they are shown at most 1360 wide """
    image = image.replace('{width}', '960')
    image = re.sub(r'([?&])imwidth=\d+', r'\g<1>imwidth=960', image)
    if 'api.ardmediathek.de/image-service' in image:
        image = re.sub(r'([?&])w=\d+', r'\g<1>w=960', image)
    image = re.sub(r'~1920x1080', '~1280x720', image)
    # arte burns its logo into the image, the tiles show the logo of the channel already
    image = image.replace('watermark=true', 'watermark=false')
    # arte only delivers its own sizes
    image = re.sub(r'/1920x1080(\?|$)', r'/940x530\1', image)
    return image


def _loads(url):
    """ True if the url delivers an image """
    try:
        response = urlopen(Request(_quote_url(url), headers={'User-Agent': USER_AGENT}), timeout=TIMEOUT)
        try:
            return response.headers.get('Content-Type', '').startswith('image/') and bool(response.read(1))
        finally:
            response.close()
    # pylint: disable=broad-except
    except Exception:
        return False


def _checked(image):
    """ The smaller size of an image if the server delivers it, the original otherwise, '' if neither loads """
    sized = _sized(image)
    if _loads(sized):
        return sized
    if sized != image and _loads(image):
        return image
    return ''


def find_image(website):
    """ Image of the page of a film, '' if there is none """
    match = _ARD_ID.search(website.split('?')[0])
    if match:
        # the API of the ARD Mediathek answers much faster than its pages
        try:
            data = json.loads(_get('https://api.ardmediathek.de/page-gateway/pages/ard/item/%s?devicetype=pc&embedded=false'
                                   % match.group(1), limit=3000000))
            for widget in data.get('widgets') or []:
                src = (widget.get('image') or {}).get('src')
                if src:
                    return _checked(src.replace('{width}', '1920'))
        # pylint: disable=broad-except
        except Exception:
            pass
    page = _get(website)
    for pattern in _OG_IMAGE:
        match = pattern.search(page)
        if match:
            image = unescape(match.group(1)).strip()
            if image.startswith('//'):
                image = 'https:' + image
            # phoenix delivers the template of its page
            if image.startswith('http') and '{{' not in image:
                return _checked(image)
    return ''


class ImageStore(object):
    """
    websites.db: index from the video url to the website, rebuilt by the service
    images.db: images found on the websites and the images of the shows
    Both are in the profile of the add-on and are not replaced with the database of the films.
    """

    def __init__(self, datapath):
        self.sites_path = os.path.join(datapath, 'websites.db')
        self.images_path = os.path.join(datapath, 'images.db')

    @staticmethod
    def _connect_sites(path):
        conn = sqlite3.connect(path, timeout=30)
        conn.execute('CREATE TABLE IF NOT EXISTS site (key INTEGER PRIMARY KEY, website TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT)')
        return conn

    def _connect_images(self):
        conn = sqlite3.connect(self.images_path, timeout=30)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE IF NOT EXISTS image (key INTEGER PRIMARY KEY, url TEXT NOT NULL, checked INTEGER NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS show (showid TEXT PRIMARY KEY, url TEXT NOT NULL)')
        return conn

    # Lookups of the interface

    def cached(self, keys, showids=()):
        """ ({key: image url}, {showid: image url}) of what is known, without network access """
        keys = [k for k in set(keys) if k]
        showids = [s for s in set(showids) if s]
        images, shows = {}, {}
        if not os.path.isfile(self.images_path):
            return images, shows
        conn = self._connect_images()
        try:
            for chunk in _chunks(keys, 500):
                for key, url in conn.execute("SELECT key, url FROM image WHERE url <> '' AND key IN (%s)"
                                             % ','.join('?' * len(chunk)), chunk):
                    images[key] = url
            for chunk in _chunks(showids, 500):
                for showid, url in conn.execute('SELECT showid, url FROM show WHERE showid IN (%s)'
                                                % ','.join('?' * len(chunk)), chunk):
                    shows[showid] = url
        finally:
            conn.close()
        return images, shows

    def resolve(self, key, showid=''):
        """ Image of a film from its website, '' if it has none or the website is unknown """
        conn = self._connect_images()
        try:
            row = conn.execute('SELECT url, checked FROM image WHERE key = ?', (key,)).fetchone()
        finally:
            conn.close()
        if row and (row[0] or time.time() - row[1] < RETRY_SECONDS):
            return row[0]
        website = self.website(key)
        if not website:
            return ''
        try:
            image = find_image(website)
        # pylint: disable=broad-except
        except Exception:
            image = ''
        conn = self._connect_images()
        try:
            with conn:
                conn.execute('INSERT OR REPLACE INTO image VALUES (?, ?, ?)', (key, image, int(time.time())))
                if image and showid:
                    # the first image found for a show stands for its episodes without one
                    conn.execute('INSERT OR IGNORE INTO show VALUES (?, ?)', (showid, image))
        finally:
            conn.close()
        return image

    def website(self, key):
        if not os.path.isfile(self.sites_path):
            return ''
        for _ in range(3):
            try:
                conn = self._connect_sites(self.sites_path)
                try:
                    row = conn.execute('SELECT website FROM site WHERE key = ?', (key,)).fetchone()
                finally:
                    conn.close()
                return row[0] if row else ''
            except sqlite3.Error:
                # the service is replacing the file
                time.sleep(0.5)
        return ''

    # Index of the websites, kept by the service

    def refresh(self, abort=lambda: False, logger=None):
        """ The full film list once a week, the list of changes once an hour """
        meta = {}
        if os.path.isfile(self.sites_path):
            conn = self._connect_sites(self.sites_path)
            try:
                meta = dict(conn.execute('SELECT name, value FROM meta').fetchall())
            finally:
                conn.close()
        now = time.time()
        if now - float(meta.get('full', 0)) > FULL_INTERVAL:
            self._import(FULL_LIST, True, abort, logger)
        elif now - float(meta.get('diff', 0)) > DIFF_INTERVAL:
            self._import(DIFF_LIST, False, abort, logger)

    def _import(self, name, full, abort, logger):
        """ The full list is written to a new file that replaces the index, the changes are added to it """
        start = time.time()
        count = 0
        path = self.sites_path + '.new' if full else self.sites_path
        with _LOCK:
            if full and os.path.isfile(path):
                os.remove(path)
            conn = self._connect_sites(path)
            try:
                if full:
                    conn.execute('PRAGMA journal_mode=OFF')
                    conn.execute('PRAGMA synchronous=OFF')
                response = urlopen(Request(FILMLISTE_URL + name, headers={'User-Agent': USER_AGENT}), timeout=60)
                try:
                    batch = []
                    for video, website in _parse_film_list(response, abort):
                        batch.append((video_key(video), website))
                        if len(batch) >= 10000:
                            conn.executemany('INSERT OR REPLACE INTO site VALUES (?, ?)', batch)
                            count += len(batch)
                            batch = []
                    conn.executemany('INSERT OR REPLACE INTO site VALUES (?, ?)', batch)
                    count += len(batch)
                finally:
                    response.close()
                if abort():
                    conn.rollback()
                    return
                now = str(time.time())
                conn.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', ('diff', now))
                if full:
                    conn.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', ('full', now))
                conn.commit()
            finally:
                conn.close()
            if full:
                _replace(path, self.sites_path)
        if logger:
            logger.info('Imported {} websites from {} in {} sec', count, name, int(time.time() - start))


def _replace(source, target):
    """ Windows refuses to replace a file that is open, the interface opens it only briefly """
    for attempt in range(20):
        try:
            os.replace(source, target)
            return
        except OSError:
            if attempt == 19:
                raise
            time.sleep(0.5)


def _parse_film_list(stream, abort):
    """ (video url, website) of the films of a compressed film list, read while it is downloaded """
    decompressor = lzma.LZMADecompressor()
    decoder = codecs.getincrementaldecoder('utf-8')('replace')
    buffer = ''
    marker = '"X":'
    header = True
    while not abort():
        chunk = stream.read(1 << 18)
        if chunk:
            buffer += decoder.decode(decompressor.decompress(chunk))
            parts = buffer.split(marker)
            buffer = parts.pop()
        else:
            parts = (buffer + decoder.decode(b'', final=True)).split(marker)
        if header and parts:
            # the header with the date of the list and the names of the fields
            parts = parts[1:]
            header = False
        for part in parts:
            part = part.strip().rstrip(',').rstrip('}')
            try:
                record = json.loads(part)
            except ValueError:
                continue
            if len(record) > 9 and record[8] and record[9]:
                yield record[8], record[9]
        if not chunk:
            return


def _chunks(values, size):
    for start in range(0, len(values), size):
        yield values[start:start + size]
