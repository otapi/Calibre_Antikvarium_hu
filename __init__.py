#!/usr/bin/env python
# vim:fileencoding=utf-8:ts=4:sw=4:sta:et:sts=4:ai
from __future__ import (unicode_literals, division, absolute_import,
						print_function)

__license__   = 'GPL v3'
__copyright__ = '2011-2018, Hoffer Csaba <csaba.hoffer@gmail.com>, Kloon <kloon@techgeek.co.in>, otapi <otapigems.com>'
__docformat__ = 'restructuredtext hu'

import time
from Queue import Queue, Empty
from lxml.html import fromstring
from calibre import as_unicode
from calibre.ebooks.metadata import check_isbn
from calibre.ebooks.metadata.sources.base import Source, Option
from calibre.utils.icu import lower
from calibre.utils.cleantext import clean_ascii_chars
import lxml, sys, traceback
from calibre import browser
import urllib
from lxml.html import tostring

class Antikvarium_hu(Source):
	name					= 'Antikvarium_hu'
	description				= _('Downloads metadata and cover from antikvarium.hu')
	author					= 'Hoffer Csaba & Kloon & otapi'
	version					= (2, 0, 0)
	minimum_calibre_version = (0, 8, 0)

	capabilities = frozenset(['identify', 'cover'])
	touched_fields = frozenset(['title', 'authors', 'identifier:isbn', 'identifier:antik_hu', 'tags', 'comments', 'publisher', 'pubdate', 'series', 'language', 'languages'])
	has_html_comments = False
	supports_gzip_transfer_encoding = False


	KEY_MAX_DOWNLOADS = 'maxDownloads'
	
	options = [Option(KEY_MAX_DOWNLOADS, 'number', 3, _('Maximum number of books to get'),
                      _('The maximum number of books to process from the Antikvarium search result')),
	
	]
	
	BASE_URL = 'https://www.antikvarium.hu'
	BOOK_URL = BASE_URL + '/konyv/'
	
	def create_query(self, log, title=None, authors=None, identifiers={}):
		if title is not None:
			search_title = urllib.quote(title.encode('utf-8'))
		else:
			search_title = ''
		log.info(' Title: %s'%search_title)
		
		if authors is not None:
			search_author = urllib.quote(authors[0].encode('utf-8'))
		else:
			search_author = ''
		log.info(' Author: %s'%search_author)
		
		search_page = "https://www.antikvarium.hu/index.php?type=search&kc=%s&sz=%s&he=0&jk=0&reszletes=1&rend=kiadasevecsokk&oldaldb=60&kapelol=0&nezet=li&elist=egyebadat&interfaceid=102&oldalcount=1"%(search_title, search_author)
		return search_page
		
	def get_cached_cover_url(self, identifiers):
		url = None
		antik_id = identifiers.get('antik_hu', None)
		if antik_id is None:
			isbn = identifiers.get('isbn', None)
			if isbn is not None:
				antik_id = self.cached_isbn_to_identifier(isbn)
		if antik_id is not None:
			url = self.cached_identifier_to_cover_url(antik_id)
		return url
	def cached_identifier_to_cover_url(self, id_):
		with self.cache_lock:
			url = self._get_cached_identifier_to_cover_url(id_)
			if not url:
				# Try for a "small" image in the cache
				url = self._get_cached_identifier_to_cover_url('small/'+id_)
			return url
	def _get_cached_identifier_to_cover_url(self, id_):
		# This must only be called once we have the cache lock
		url = self._identifier_to_cover_url_cache.get(id_, None)
		if not url:
			# We could not get a url for this particular B&N id
			# However we might have one for a different isbn for this book
			# Barnes & Noble are not very consistent with their covers and
			# it could be that the particular ISBN we chose does not have
			# a large image but another ISBN we retrieved does.
			key_prefix = id_.rpartition('/')[0]
			for key in self._identifier_to_cover_url_cache.keys():
				if key.startswith('key_prefix'):
					return self._identifier_to_cover_url_cache[key]
		return url
	def identify(self, log, result_queue, abort, title, authors,
			identifiers={}, timeout=30):
		'''
		Note this method will retry without identifiers automatically if no
		match is found with identifiers.
		'''
		
		matches = []
		antik_id = identifiers.get('antik_hu', None)
		isbn = check_isbn(identifiers.get('isbn', None))
		br = browser()
		log.info(u'\nTitle:%s\nAuthors:%s\n'%(title, authors))
		if antik_id:
			matches.append('%s%s'%(Antikvarium_hu.BOOK_URL, antik_id))
		else:
			if isbn:
				matches.append('https://www.antikvarium.hu/index.php?type=search&isbn=%s'%(isbn))
			else:
				query = self.create_query(log, title=title, authors=authors, identifiers=identifiers)
				if query is None:
					log.error('Insufficient metadata to construct query')
					return
				try:
					log.info('Querying: %s'%query)
					response = br.open(query)
				except Exception as e:
					if isbn and callable(getattr(e, 'getcode', None)) and e.getcode() == 404:
						# We did a lookup by ISBN but did not find a match
						# We will fallback to doing a lookup by title author
						log.info('Failed to find match for ISBN: %s'%isbn)
					else:
						err = 'Failed to make identify query: %r'%query
						log.exception(err)
						return as_unicode(e)
						
				try:
					raw = response.read().strip()
					raw = raw.decode('utf-8', errors='replace')
					if not raw:
						log.error('Failed to get raw result for query: %r'%query)
						return
					root = fromstring(clean_ascii_chars(raw))
				except:
					msg = 'Failed to parse Antikvarium.hu page for query: %r'%query
					log.exception(msg)
					return msg
				self._parse_search_results(log, title, authors, root, matches, timeout)
			
		if abort.is_set():
			
			return
		if not matches:
			if identifiers and title and authors:
				log.info('No matches found with identifiers, retrying using only'
						' title and authors')
				return self.identify(log, result_queue, abort, title=title,
						authors=authors, timeout=timeout)
			log.error('No matches found with query: %r'%query)
			return
		from calibre_plugins.antikvarium_hu.worker import Worker
		workers = [Worker(url, result_queue, br, log, i, self) for i, url in
				enumerate(matches)]
		
		for w in workers:
			w.start()
			# Don't send all requests at the same time
			time.sleep(0.1)

		while not abort.is_set():
			a_worker_is_alive = False
			for w in workers:
				w.join(0.2)
			
				if abort.is_set():
					break
				if w.is_alive():
					a_worker_is_alive = True
			if not a_worker_is_alive:
			
				break
		return None
		
	def _parse_search_results(self, log, title, authors, root, matches, timeout):
		results = root.xpath('//*[@id="searchResultKonyvCim-listas"]/@href')
		
		max_results = self.prefs[Antikvarium_hu.KEY_MAX_DOWNLOADS]
		for result in results:
			book_url = 'https://www.antikvarium.hu/' + result
			log.info('Book URL: %r'%book_url)		
			matches.append(book_url)
			if len(matches) >= max_results:
				return
	def download_cover(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30):
		cached_url = self.get_cached_cover_url(identifiers)
		if cached_url is None:
			log.info('No cached cover found, running identify')
			rq = Queue()
			self.identify(log, rq, abort, title=title, authors=authors, identifiers=identifiers)
			if abort.is_set():
				return
			results = []
			while True:
				try:
					results.append(rq.get_nowait())
				except Empty:
					break
			results.sort(key=self.identify_results_keygen(
				title=title, authors=authors, identifiers=identifiers))
			for mi in results:
				cached_url = self.get_cached_cover_url(mi.identifiers)
				if cached_url is not None:
					break
		if cached_url is None:
			log.info('No cover found')
			return

		if abort.is_set():
			return
		br = self.browser
		log.info('Downloading cover from:', cached_url)
		try:
			cdata = br.open_novisit(cached_url, timeout=timeout).read()
			result_queue.put((self, cdata))
		except:
			log.exception('Failed to download cover from:', cached_url)