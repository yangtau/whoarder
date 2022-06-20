import argparse
import codecs
import os
from typing import Dict, List

import chardet
import re
from jinja2 import Environment, FileSystemLoader  # available from pip

DEBUG = False


class Clippings(object):
    def __init__(self, source, dest=None):
        '''
        Prepares the import and store it into the 'clippings' dict.
        '''
        self.source = source
        self.dest = self._get_default_dest() if dest is None else dest
        self.book_author_couples = ()
        self.clippings = []
        self._fetch()

    def _get_default_dest(self):
        '''
        When no destination is specified, output to <InputFilename>.html
        '''
        source_full_path = os.path.realpath(self.source)
        dirname, filename_with_ext = os.path.split(source_full_path)
        filename = os.path.splitext(filename_with_ext)[0]
        default_destination = os.path.join(dirname, filename + '.html')
        return default_destination

    def _fetch(self):
        '''
        Imports clippings and book_author_couples from the source file
        '''
        clippings = ClippingsIterator(self.source)
        for clipping in clippings:
            self.clippings.append(clipping)

        # will be useful in the HTML to group by book/author
        self.book_author_couples = set((clipping['book'], clipping['author'])
                                       for clipping in self.clippings)

    def export_to_html(self):
        '''
        Output the clippings dict to HTML, using a Jinja2 template
        '''
        env = Environment(loader=FileSystemLoader('./'))
        template = env.get_template('template1.html')
        render = template.render(clippings=self.clippings,
                                 book_author_couples=self.book_author_couples)

        with open(self.dest, mode='w', encoding='utf-8') as output:
            output.write(render)


class ClippingsIterator(object):
    '''
    Iterator that abstracts the Kindle format and spits a dict per clipping.
    A 'clipping' can be either a Highlight or a Note, and is (as far as I
    know, on my Kindle) a succession of five lines (see ex. and regexes below):
    - Lines 1 & 2 contain metadata
    - Line 3 is empty
    - Line 4 is the clipping
    - Line 5 is the separator

    Example:
    <book> (<author_last_name>, <author_first_name>)
    - Your <type> on Page <page> | Location <locs>-<loce> | Added on <date>

    <contents>
    ==========

    Chinese Example:
    我的职业是小说家 (村上春树)
    - 您在位置 #117-118的标注 | 添加于 2018年8月5日星期日 上午8:06:49

    写小说这份活计，概而言之，实在是效率低下的营生。这是一种再三重复“比如说”的作业。
    '''

    _clipping_line1 = re.compile(r'''
        ^(?P<book>.*?)                                  # Le Petit Prince
        (\ \((\[.*\])?(?P<author>[^()]*?)\ ?(\(.*\))?\))?$  # ([日]村上春树 (Haruki Murakami)) 
        ''', re.VERBOSE | re.IGNORECASE)

    _clipping_book = re.compile(r'''
            ^(.*?)                               # book
            (【.*】|\ \(.*\)|（.*）)*$           # intro
            ''', re.VERBOSE)

    _clipping_line2_english = re.compile(r'''
        ^-\ (?:Your\ )?(?P<type>\w*)                         # Your Highlight
        (\ (?:on\ )?(?P<page>Unnumbered\ Page|Page\ .*)      #  on Page 42 |
        \ \|)?
        (?:\ This\ Article)?                                 #  This Article
        \ (?:on\ |at\ )?(Location|Loc\.)\ (?P<location>.*)   #  Location 123-321
        \ \|\ Added\ on\ (?P<date>.*)$                       #  | Added on...
        ''', re.VERBOSE | re.IGNORECASE)

    # - 您在第 1656 页（位置 #18097-18097）的标注 | 添加于 2022年1月9日星期日 下午11:11:58
    _clipping_line2_chinese = re.compile(r'''
        ^-\ (您在)(第\ (?P<page>\w*)\ 页)?(（?位置\ \#(?P<location>.*?)）?)?                         
        的(?P<type>(标注|笔记))
        \ \|\ 添加于\ (?P<date>.*)$                          #  | 添加于 ...
        ''', re.VERBOSE | re.IGNORECASE)

    _delimiter = '=========='

    # ^-\ (您在)(第\ (?P<page>)\ 页)?(（?位置\ \#(?P<location>)）?)?的(?P<type>(标注|笔记))\ \|\ 添加于\ (?P<date>.*)$

    def __init__(self, source):
        self.lines = readlines(source)
        self.cursor = 0

    def __iter__(self):
        return self

    def __next__(self) -> Dict[str, str]:
        if self.cursor >= len(self.lines):
            raise StopIteration

        start = self.cursor
        self.cursor = self.lines.index(self._delimiter, start)
        if start == self.cursor or self.cursor == len(self.lines):
            raise StopIteration

        cur = self.lines[start:self.cursor]
        self.cursor += 1
        try:
            return self.__parse(cur)
        except Exception as e:
            raise InvalidFormatException(
                "Exception: {}\nFailed to parse: {}".format(e, cur))

    def __parse(self, content: List[str]) -> Dict[str, str]:
        '''
        Parses the content of a clipping and returns a dict with the
        '''
        if len(content) < 4:
            raise ValueError(
                'Invalid clipping, less than 4 lines:\n{}'.format(content))

        # 1. Parse the first line
        result = unwrap(self._clipping_line1.search(content[0])).groupdict()
        book = result['book']
        if book:
            result['book'], _ = unwrap(
                self._clipping_book.match(book.strip())).groups()

        # 2. Parse the second line
        zh = self._clipping_line2_chinese.search(content[1])
        if zh:
            dict = zh.groupdict()
            dict['type'] = 'Highlight' if dict['type'] == '标注' else 'Note'
            dict['page'] = 'page ' + dict['page'] if dict['page'] else None
        else:
            dict = unwrap(
                self._clipping_line2_english.search(content[1])).groupdict()
        result.update(dict)

        # 3. Parse the content
        assert content[2] == '', 'Invalid clipping, line 3 is not empty'
        result["contents"] = '\n'.join(content[3:]).strip()

        if DEBUG:
            print(content)
            print(result)
        return result


def unwrap(result: None | re.Match[str]) -> re.Match[str]:
    match result:
        case None:
            raise Exception('No result')
        case _:
            return result


def readlines(source) -> List[str]:
    '''
    Returns the encoding of the source file, using chardet.
    '''
    with open(source, "rb") as f:
        rawdata = f.read()
        # chardet detects UTF-8 with BOM as 'UTF-8' (I don't know why), i.e.
        # fails to notify us about the BOM, resulting in a string prepended
        # with \ufeff, so we manually detect and set the utf-8-sig encoding
        if rawdata.startswith(codecs.BOM_UTF8):
            detected_encoding = 'utf-8-sig'
        else:
            result = chardet.detect(rawdata)
            detected_encoding = result['encoding']
        return codecs.decode(rawdata, detected_encoding).splitlines()


class InvalidFormatException(BaseException):
    pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="whoarder converts Kindle \
                        'My Clippings.txt' files to more pleasant HTML.")
    parser.add_argument('source',
                        help='Path to the source file, stored by Kindle in \
                        /Media/Kindle/documents/My Clippings.txt.')
    parser.add_argument('destination',
                        help='Target HTML file. If omitted, a .html bearing \
                        the same name as the input .txt file will be used.',
                        nargs='?', default=None)
    args = parser.parse_args()

    clippings = Clippings(args.source, args.destination)
    clippings.export_to_html()
    print('Successfully wrote ' + clippings.dest + "\n")
