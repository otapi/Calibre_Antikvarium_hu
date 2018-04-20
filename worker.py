#!/usr/bin/env python
# vim:fileencoding=utf-8:ts=4:sw=4:sta:et:sts=4:ai
from __future__ import (unicode_literals, division, absolute_import,
						print_function)

__license__   = 'GPL v3'
__copyright__ = '2011-2012, Hoffer Csaba <csaba.hoffer@gmail.com>, Kloon <kloon@techgeek.co.in>'
__docformat__ = 'restructuredtext hu'

import socket, re
from threading import Thread
from calibre.ebooks.metadata.book.base import Metadata
import lxml, sys
import lxml.html as lh
from calibre.utils.date import utcnow
from datetime import datetime
from dateutil import parser
from calibre.ebooks.metadata import MetaInformation
from calibre import browser


class Worker(Thread): # Get details

	'''
	Get book details from antikvarium.hu book page in a separate thread
	'''

	def __init__(self, url, result_queue, browser, log, relevance, plugin, timeout=30):
		Thread.__init__(self)
		self.daemon = True
		self.url, self.result_queue = url, result_queue
		self.log, self.timeout = log, timeout
		self.relevance, self.plugin = relevance, plugin
		self.browser = browser.clone_browser()
		self.cover_url = self.antik_id = self.isbn = None

	def run(self):
		try:
			self.get_details()
		except:
			self.log.exception('get_details failed for url: %r'%self.url)

	def get_details(self):
		
		try:
			raw = self.browser.open_novisit(self.url, timeout=self.timeout)
		except Exception as e:
			if callable(getattr(e, 'getcode', None)) and e.getcode() == 404:
				self.log.error('URL malformed: %r'%self.url)
				return
			attr = getattr(e, 'args', [None])
			attr = attr if attr else [None]
			if isinstance(attr[0], socket.timeout):
				msg = 'Antikvarium.hu timed out. Try again later.'
				self.log.error(msg)
			else:
				msg = 'Failed to make details query: %r'%self.url
				self.log.exception(msg)
			return

		root = lh.parse(raw)
		self.parse_details(root)
		
		

	def parse_details(self, root):
		try:
			antik_id = self.parse_antik_id(root)
			self.log.info('Parsed Antikvarium identifier: %s'%antik_id)
		except:
			self.log.exception('Error parsing Antikvarium id for url: %r'%self.url)
			antik_id = None

		try:
			title = self.parse_title(root)
			self.log.info('Parsed title: %s'%title)
		except:
			self.log.exception('Error parsing title for url: %r'%self.url)
			title = None
		
		try:
			authors = self.parse_authors(root)
			self.log.info('Parsed authors: %s'%authors)
		except:
			self.log.exception('Error parsing authors for url: %r'%self.url)
			authors = []

		if not title or not authors or not antik_id:
			self.log.error('Could not find title/authors/Antikvarium.hu id for %r'%self.url)
			self.log.error('Antikvarium.hu id: %r Title: %r Authors: %r'%(antik_id, title, authors))
			return

		mi = Metadata(title, authors)
		mi.set_identifier('antik_hu', antik_id)
		self.antik_id = antik_id

		try:
			isbn = self.parse_isbn(root)
			self.log.info('Parsed ISBN: %s'%isbn)
			if isbn:
				self.isbn = mi.isbn = isbn
		except:
			self.log.exception('Error parsing ISBN for url: %r'%self.url)

		try:
			series = self.parse_series(root)
			self.log.info('Parsed series: %s'%series)
		except :
			self.log.exception('Error parsing series for url: %r'%self.url)
			series = None
			
		try:
			mi.series_index = self.parse_series_index(root)
			self.log.info('Parsed series index: %s'%mi.series_index)
		except :
			self.log.exception('Error parsing series for url: %r'%self.url)
			mi.series_index = None
			
		try:
			mi.comments = self.parse_comments(root)
			self.log.info('Parsed comments: %s'%mi.comments)
		except:
			self.log.exception('Error parsing comments for url: %r'%self.url)

		try:
			self.cover_url = self.parse_cover(root)
			self.log.info('Parsed URL for cover: %r'%self.cover_url)
			self.plugin.cache_identifier_to_cover_url(self.antik_id, self.cover_url)
			mi.has_cover = bool(self.cover_url)
		except:
			self.log.exception('Error parsing cover for url: %r'%self.url)

		try:
			mi.publisher = self.parse_publisher(root)
			self.log.info('Parsed publisher: %s'%mi.publisher)
		except:
			self.log.exception('Error parsing publisher for url: %r'%self.url)
			
		try:
			mi.tags = self.parse_tags(root)
			self.log.info('Parsed tags: %s'%mi.tags)
		except:
			self.log.exception('Error parsing tags for url: %r'%self.url)

		try:
			mi.pubdate = self.parse_published_date(root)
			self.log.info('Parsed publication date: %s'%mi.pubdate)
		except:
			self.log.exception('Error parsing published date for url: %r'%self.url)
			
		try:
			mi.languages = self.parse_languages(root)
			self.log.info('Parsed languages: %r'%mi.languages)
		except:
			self.log.exception('Error parsing languages for url: %r'%self.url)

		mi.source_relevance = self.relevance

		if series:
			mi.series = series

		if self.antik_id and self.isbn:
			self.plugin.cache_isbn_to_identifier(self.isbn, self.antik_id)

		
		self.plugin.clean_downloaded_metadata(mi)

		self.result_queue.put(mi)

	def parse_antik_id(self, root):
		try:
			antik_id_node = root.xpath('/html/head/link/@href')
			for antik_id in antik_id_node:
				m = re.search('/konyv/(.*)', antik_id)
				if m:
					return m.group(1)
		except:
			return None
		
	def book_property(self, root, search_data):
		for i in range(1, 12):
			try:
				data = root.xpath('//*[@class="book-data-table"]//tr[%d]/th//text()'%i)
				if data:
					data_text = data[0].strip()
					if data_text == search_data:
						data_nodes = root.xpath('//*[@class="book-data-table"]//tr[%d]/td//text()'%i)
						for data in data_nodes:
							if data.strip(' \r\n\t'):
								return data.strip(' \r\n\t')
				i = i + 1
			except:
				return None
		
		'''try:
			data = root.xpath('//*[@id="konyvadat_adatok"]//tr[%d]/td[2]//text()'%index)
			if data:
				return data.strip()
				return [unicode(data_node.strip(' \r\n\t')) for data_node in data_nodes]
		except:
			return None'''
		
	def parse_title(self, root):
		title_node = root.xpath('//*[@class="book-data-title-height"]//text()')
		return title_node[0].strip(' \r\n\t')
			
	def parse_series(self, root):
		try:
			search_data = "Sorozatcím:"
			isbn = self.book_property(root, search_data)
			if isbn:
				return isbn.replace('-', '')
		except:
			return None
		
		
	def parse_series_index(self, root):
		try:
			search_data = "Kötetszám:"
			isbn = self.book_property(root, search_data)
			if isbn:
				return isbn.replace('-', '')
		except:
			return None

	def parse_authors(self, root):
		try:
			author_nodes = root.xpath('//*[@class="book-data-author"]//text()')
			authors = []
			for i in range(0, len(author_nodes), 1):
				if author_nodes[i].strip(' \r\n\t'):
					authors.append(author_nodes[i].strip(' \r\n\t'))
			return authors
		except:
			return None

	def parse_isbn(self, root):
		try:
			search_data = "ISBN:"
			isbn = self.book_property(root, search_data)
			if isbn:
				return isbn.replace('-', '')
		except:
			return None
	def parse_publisher(self, root):
		try:
			search_data = "Kiadó:"
			publisher_node = self.book_property(root, search_data)
			if publisher_node:
				return publisher_node
		except:
			return None

	def parse_published_date(self, root):
		try:
			search_data = "Kiadás éve:"
			pub_year_node = self.book_property(root, search_data)
			if pub_year_node:
				default = datetime.utcnow()
				from calibre.utils.date import utc_tz
				default = datetime(default.year, default.month, default.day, tzinfo=utc_tz)
				pub_date = parser.parse(pub_year_node[0], default=default)
				return pub_date
		except:
			return None
			
	def parse_tags(self, root):
		try:
			tag_nodes = root.xpath('//*[@id="konyvAdatlapTemakorLink"]/span/text()')
			tags = []
			for i in range(0, len(tag_nodes), 1):
				tag = tag_nodes[i]
				if tag != 'Tartalom szerint' and tag != 'Egyéb' and tag != 'Az író származása szerint':
					if tag not in tags:
						tags.append(tag)
			
			return tags
		except:
			return None
	
	def parse_comments(self, root):
		try:
			comments = root.xpath('//*[@id="fulszovegShort"]//text()[2]')
			if comments:
				return comments[1].strip(' \r\n\t')
			comments = root.xpath('//*[@id="eloszoFull"]//text()[2]')
			if comments:
				return comments[1].strip(' \r\n\t')
				
			
		except:
			return None
	
	def parse_languages(self, root):
		try:
			search_data = "Nyelv:"
			lang_node = self.book_property(root, search_data)
			
			return [self._translateLanguageToCode(lang.lower()) for lang in lang_node.split(',')]
		except:
			return None

	def parse_cover(self, root):
		try:
			book_cover = root.xpath('//*[@class="konyvadatlapfoto"]/img/@src')
			if book_cover:
				return 'https://www.antikvarium.hu/%s'%(book_cover[0])
		except:
			return None
		
	def _translateLanguageToCode(self, displayLang):
		displayLang = displayLang.strip() if displayLang else None
		langTbl = { None: 'und',
					u'magyar': 'hu', 
					u'angol': 'en', 
					u'amerikai': 'en',
					u'amerikai angol': 'en', 
					u'n\xe9met': 'de', 
					u'francia': 'fr',
					u'olasz': 'it', 
					u'spanyol': 'es',
					u'orosz': 'ru',
					u't\xf6r\xf6k': 'tr',
					u'g\xf6r\xf6g': 'gr',
					u'k\xednai': 'cn' }
		return langTbl.get(displayLang, None)
		