# -*- coding: UTF-8 -*-
# Copyright (C) 2007 Sylvain Taverne <sylvain@itaapy.com>
# Copyright (C) 2007-2008 Juan David Ibáñez Palomar <jdavid@itaapy.com>
# Copyright (C) 2007-2008, 2010 Henry Obein <henry@itaapy.com>
# Copyright (C) 2007-2008, 2010 Hervé Cauwelier <herve@itaapy.com>
# Copyright (C) 2008 Gautier Hayoun <gautier.hayoun@itaapy.com>
# Copyright (C) 2014 Nicolas Deram <nicolas@agicia.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# Import from the Standard Library
from cStringIO import StringIO
from datetime import datetime, timedelta
from re import compile
from subprocess import call
from tempfile import mkdtemp

# Import from docutils
from docutils.core import Publisher, publish_from_doctree, publish_string
from docutils.io import StringOutput, DocTreeInput
from docutils.readers.doctree import Reader
from docutils.utils import SystemMessage
from docutils import nodes

# Import from itools
from itools.database import PhraseQuery
from itools.datatypes import Enumerate, Boolean, XMLContent
from itools.gettext import MSG
from itools.handlers import checkid, TextFile, File
from itools.html import XHTMLFile
from itools.i18n import format_datetime
from itools.uri import get_reference
from itools.uri.mailto import Mailto
from itools.fs import lfs, FileName
from itools.web import BaseView, STLView, ERROR, get_context
from itools.xml import XMLParser, XMLError

# Import from ikaaro
from ikaaro import messages
from ikaaro.autoedit import AutoEdit
from ikaaro.autoform import AutoForm
from ikaaro.datatypes import FileDataType
from ikaaro.fields import File_Field
from ikaaro.widgets import FileWidget, RadioWidget
from ikaaro.views import ContextMenu
from ikaaro.widgets import MultilineWidget


figure_style_converter = compile(r'\\begin\{figure\}\[.*?\]')
ALLOWED_FORMATS = ('application/vnd.oasis.opendocument.text',
        'application/vnd.oasis.opendocument.text-template')


ERR_SYNTAX_WARNING = ERROR(u"Syntax error, please check the view for "
        u"details.")
ERR_SYNTAX_ERROR = ERROR(u'Syntax error: {error}')
ERR_PDFLATEX_MISSING = ERROR(u"PDF generation failed. Please install the "
        u"pdflatex binary on the server.")
ERR_PDF_BOOK = ERROR(u"Books are not exportable to PDF.")
ERR_PDF_FAILED = ERROR(u'PDF generation failed. See "{dirname}" on the '
        u'server for debug.')
ERR_PDF_NOT_FOUND = ERROR(u'PDF generated not found. See "{dirname}" on the '
        u'server for debug.')
ERR_NOT_ODT = ERROR(u"{filename} is not an OpenDocument Text.")
ERR_PAGE_NOT_FOUND = ERROR(u'Page "{uri}" not found.')
ERR_NO_PAGE_FOUND = ERROR(u"No page found to export.")


def is_external(reference):
    return (type(reference) is Mailto or reference.scheme
            or reference.authority)



def default_reference_resolver(resource, reference, context):
    """Make canonical URI to the website for future download.
    """
    # Resolve against the parent
    uri = context.uri.resolve('..')
    return str(uri.resolve2(reference))



def resolve_references(doctree, resource, context,
        reference_resolver=default_reference_resolver, **kw):
    """Translate resource path to accessible path in the output document.
    """
    for node in doctree.traverse(condition=nodes.reference):
        wiki_name = node.get('wiki_name')
        if wiki_name is False:
            # Broken link
            continue
        elif wiki_name:
            # Wiki link
            reference = get_reference(wiki_name)
            node['refuri'] = reference_resolver(resource, reference, context,
                    **kw)
        elif wiki_name is None:
            # Regular link: point back to the site
            refuri = node.get('refuri')
            if refuri is None:
                continue
            reference = get_reference(refuri.encode('utf_8'))
            if is_external(reference):
                # Keep the unicode version
                continue
            node['refuri'] = reference_resolver(resource, reference, context,
                    **kw)



def resolve_images(doctree, resource, context):
    """Translate image path to handler key to load them from filesystem.
    """
    fs = resource.metadata.database.fs
    for node in doctree.traverse(condition=nodes.image):
        reference = get_reference(node['uri'].encode('utf8'))
        if is_external(reference):
            continue
        name = str(reference.path)
        image = resource.get_resource(name, soft=True)
        if image is not None:
            node['uri'] = fs.get_absolute_path(image.handler.key)



class BacklinksMenu(ContextMenu):
    title = MSG(u"Backlinks")


    def get_items(self):
        query = PhraseQuery('links', str(self.resource.abspath))
        results = self.context.search(query)
        return [ {'title': x.get_title(),
                  'href': str(x.abspath),
                  'src': x.get_class_icon()}
                 for x in results.get_resources(sort_by='mtime') ]



#
# To ODT Tools
#

def odt_reference_resolver(resource, reference, context, known_links=[]):
    path = reference.path
    if not path.startswith_slash:
        # Was not resolved
        raise ValueError, 'page "%s": the link "%s" is broken' % (
                resource.name, reference)
    if path not in known_links:
        return default_reference_resolver(resource, reference, context)
    destination = resource.get_resource(path)
    if not isinstance(destination, resource.__class__):
        return default_reference_resolver(resource, reference, context)
    doctree = destination.get_doctree()
    title = None
    if reference.fragment:
        for node in doctree.traverse(condition=nodes.title):
            if checkid(node.astext()) == reference.fragment:
                title = node.astext()
                break
    if not reference.fragment or title is None:
        # First title
        for node in doctree.traverse(condition=nodes.title):
            title = node.astext()
            break
    # No title at all
    if title is None:
        return default_reference_resolver(resource, reference, context)
    return u"#1.%s|outline" % title



def startswith_section(doctree):
    """Return if the doctree starts with a section or directly a title, used
    to fix heading levels.
    """
    if not doctree.children:
        return False
    for node in doctree.traverse():
        # Don't use "type()" with docutils 0.5
        if isinstance(node, nodes.section):
            return True
        elif isinstance(node, nodes.title):
            # Ignore <tile> inside <topic>
            if isinstance(node.parent, nodes.topic):
                continue
            return False
    return False



def convert_cover_title(node, context):
    from lpod.rst2odt import convert_paragraph

    level = context['heading_level'] or 1
    if node.tagname == 'subtitle':
        style = 'Subtitle'
        context['heading_level'] += 1
    else:
        style = ('sub' * (level - 1) + 'title').capitalize()
    context['styles']['paragraph'] = style
    convert_paragraph(node, context)
    del context['styles']['paragraph']



class PageVisitor(nodes.SparseNodeVisitor):

    def __init__(self, doctree, container, ignore_missing_pages=False):
        nodes.NodeVisitor.__init__(self, doctree)
        self.container = container
        self.ignore_missing_pages = ignore_missing_pages
        self.pages = []
        self.level = 0


    def visit_enumerated_list(self, node):
        self.level += 1


    def depart_enumerated_list(self, node):
        self.level -= 1


    def visit_list_item(self, node):
        reference = node.next_node(condition=nodes.reference)
        if reference is None:
            return
        path = reference.get('wiki_name')
        if path is False:
            if self.ignore_missing_pages is True:
                return
            raise LookupError, node.astext()
        page = self.container.get_resource(path)
        self.pages.append((page, self.level))



class TemplateList(Enumerate):

    @classmethod
    def get_options(cls):
        context = get_context()
        container = context.resource.parent

        options = [{'name': '', 'value': MSG(u"lpoD default template")}]
        for resource in container.get_resources():
            if not resource.class_id in ALLOWED_FORMATS:
                continue
            msg = MSG(u'{title} (<a href="{link}">view</a>)')
            msg = msg.gettext(title=resource.get_title(),
                    link=context.get_link(resource)).encode('utf_8')
            options.append({'name': resource.name, 'value': XMLParser(msg)})
        return options



class WikiPage_View(BaseView):
    access = 'is_allowed_to_view'
    title = MSG(u'View')
    icon = 'html.png'
    styles = ['/ui/wiki/style.css']


    def GET(self, resource, context):
        parent = resource.parent

        try:
            doctree = resource.get_doctree()
        except SystemMessage, e:
            # Critical
            context.message = ERR_SYNTAX_ERROR(error=e.message)
            content = XMLContent.encode(resource.handler.to_str())
            return '<pre>' + content + '</pre>'

        # Decorate the links and resolve them against the published resource
        for node in doctree.traverse(condition=nodes.reference):
            refname = node.get('wiki_name')
            if refname is None:
                # Regular link
                if node.get('refid'):
                    node['classes'].append('internal')
                refuri = node.get('refuri')
                if refuri is None:
                    continue
                reference = get_reference(refuri.encode('utf_8'))
                # Skip external
                if is_external(reference):
                    node['classes'].append('external')
                    continue
                destination = resource.get_resource(reference.path,
                        soft=True)
                if destination is None:
                    destination = parent.get_resource(reference.path,
                            soft=True)
                if destination is None:
                    resource.set_new_resource_link(node)
                    continue
                refuri = context.get_link(destination)
                if reference.fragment:
                    refuri = "%s#%s" % (refuri, reference.fragment)
                node['refuri'] = refuri
            elif refname is False:
                # Wiki link not found
                resource.set_new_resource_link(node)
            else:
                # Wiki link found, "refname" is the path
                node['classes'].append('wiki')
                destination = resource.get_resource(refname)
                node['refuri'] = context.get_link(destination)

        # Download images directly
        for node in doctree.traverse(condition=nodes.image):
            reference = get_reference(node['uri'].encode('utf_8'))
            if is_external(reference):
                continue
            destination = resource.get_resource(reference.path, soft=True)
            if destination is None:
                continue
            node['uri'] = '%s/;download' % context.get_link(destination)

        # Manipulate publisher directly (from publish_from_doctree)
        reader = Reader(parser_name='null')
        pub = Publisher(reader, None, None, source=DocTreeInput(doctree),
                destination_class=StringOutput)
        pub.set_writer('html')
        pub.process_programmatic_settings(None, resource.overrides, None)
        pub.set_destination(None, None)
        pub.publish(enable_exit_status=None)
        parts = pub.writer.parts
        body = parts['html_body']

        return body.encode('utf_8')



class WikiPage_ToPDF(BaseView):
    access = 'is_allowed_to_view'
    title = MSG(u"To PDF")


    def GET(self, resource, context):
        # Check if pdflatex exists
        try:
            call(['pdflatex', '-version'])
        except OSError:
            msg = ERR_PDFLATEX_MISSING
            return context.come_back(msg)

        doctree = resource.get_doctree()
        # We hack a bit the document tree to enhance the PDF produced
        resolve_references(doctree, resource, context)
        resolve_images(doctree, resource, context)

        # Make some modifications
        overrides = dict(resource.overrides)
        overrides['stylesheet'] = 'style.tex'
        try:
            output = publish_from_doctree(doctree, writer_name='latex',
                    settings_overrides=overrides)
        except NotImplementedError, e:
            if str(e).endswith('visiting unknown node type: book'):
                message = ERR_PDF_BOOK
                return context.come_back(message)
            raise
        output = figure_style_converter.sub(r'\\begin{figure}[H]', output)

        dirname = mkdtemp('wiki', 'itools')
        tempdir = lfs.open(dirname)

        # Save the document...
        file = tempdir.make_file(resource.name)
        try:
            file.write(output)
        finally:
            file.close()
        # The stylesheet...
        stylesheet = resource.get_resource('/ui/wiki/style.tex')
        file = tempdir.make_file('style.tex')
        try:
            stylesheet.save_state_to_file(file)
        finally:
            file.close()
        # The 'powered' image...
        image = resource.get_resource('/ui/aruni/images/ikaaro_powered.png')
        file = tempdir.make_file('ikaaro.png')
        try:
            image.save_state_to_file(file)
        finally:
            file.close()
        # The stub for not found images
        stub = resource.get_resource('/ui/wiki/missing.png')
        # And referenced images
        for node in doctree.traverse(condition=nodes.image):
            uri = node['uri']
            filename = uri.rsplit('/', 1)[-1]
            # pdflatex does not support the ".jpeg" extension
            name, ext, lang = FileName.decode(filename)
            if ext == 'jpeg':
                filename = FileName.encode((name, 'jpg', lang))
            if tempdir.exists(filename):
                continue
            file = tempdir.make_file(filename)
            try:
                try:
                    handler = File(uri)
                except LookupError:
                    handler = stub
                try:
                    handler.save_state_to_file(file)
                except XMLError:
                    # XMLError is raised by unexpected HTTP responses
                    # from external images. See bug #249
                    pass
            finally:
                file.close()
            # Remove all path so the image is found in tempdir
            node['uri'] = filename

        # From LaTeX to PDF
        command = ['pdflatex', '-8bit', '-no-file-line-error',
                   '-interaction=batchmode', resource.name]
        try:
            call(command, cwd=dirname)
            # Twice for correct page numbering
            call(command, cwd=dirname)
        except OSError:
            msg = ERR_PDF_FAILED(dirname=dirname)
            return context.come_back(msg)

        pdfname = '%s.pdf' % resource.name
        if not tempdir.exists(pdfname):
            msg = ERR_PDF_NOT_FOUND(dirname=dirname)
            return context.come_back(msg)

        # Read the file's data
        file = tempdir.open(pdfname)
        try:
            data = file.read()
        finally:
            file.close()

        # Clean the temporary folder
        lfs.remove(dirname)

        # Ok
        context.set_content_type('application/pdf')
        context.set_content_disposition('inline', pdfname)
        return data



class WikiPage_Edit(AutoEdit):

    # No Multilingual
    action_goto = './;view'

    data = File_Field(required=True, class_handler=TextFile,
        widget=MultilineWidget(rows=20, cols=80))
    fields = ['data', 'title', 'share']


    def get_value(self, resource, context, name, datatype):
        if name == 'data':
            value = resource.get_value('data')
            if value:
                return value.to_str()
            return None
        proxy = super(WikiPage_Edit, self)
        return proxy.get_value(resource, context, name, datatype)


    def set_value(self, resource, context, name, form):
        proxy = super(WikiPage_Edit, self)
        if name == 'data':
            proxy.set_value(resource, context, name, form)
            # Warn about syntax errors
            message = None
            try:
                html = resource.view.GET(resource, context)
            except SystemMessage, e:
                # Critical
                message = ERR_SYNTAX_ERROR(error=e.message)
            else:
                # Non-critical
                if 'class="system-message"' in html:
                    message = ERR_SYNTAX_WARNING
                else:
                    accept = context.accept_language
                    time = format_datetime(datetime.now(), accept=accept)
                    message = messages.MSG_CHANGES_SAVED2(time=time)
            # Keep it to override context.message later
            context.wiki_message = message
            # Save even if broken
            return False
        return proxy.set_value(resource, context, name, form)



class WikiPage_ToODT(AutoForm):
    access = 'is_allowed_to_view'
    title = MSG(u"To ODT")
    schema = {'template': TemplateList,
            'template_upload': FileDataType,
            'ignore_missing_pages': Boolean}
    widgets = [RadioWidget('template', title=MSG(u"Choose a template:"),
            has_empty_option=False),
        FileWidget('template_upload',
            title=MSG(u"Or provide another ODT as a template:")),
        RadioWidget('ignore_missing_pages',
            title=MSG(u"Ignore missing pages"))]
    submit_value = MSG(u"Convert")


    def get_value(self, resource, context, name, datatype):
        if name == 'template':
            book = resource.get_book()
            if book is not None:
                template = book.get('template')
                if template is not None:
                    return template
        elif name == 'ignore_missing_pages':
            book = resource.get_book()
            if book is not None:
                ignore_missing_pages = book.get('ignore-missing-pages')
                return ignore_missing_pages == 'yes'
        proxy = super(WikiPage_ToODT, self)
        return proxy.get_value(resource, context, name, datatype)


    def GET(self, resource, context):
        try:
            from lpod.rst2odt import rst2odt
        except ImportError:
            msg = MSG(u'<p>Please install <a href="{href}">{name}</a> '
                      u'for Python on the server.</p>')
            msg = msg.gettext(href='http://lpod-project.org/', name='LpOD')
            return msg.encode('utf_8')
        # Just to ignore pyflakes warning
        rst2odt
        proxy = super(WikiPage_ToODT, self)
        return proxy.GET(resource, context)


    def action(self, resource, context, form):
        from lpod.document import odf_get_document
        from lpod.document import odf_new_document_from_type
        from lpod.rst2odt import rst2odt, convert, make_context
        from lpod.toc import odf_create_toc

        parent = resource.parent
        template = None
        template_upload = form['template_upload']
        if template_upload is not None:
            filename, mimetype, body = template_upload
            if mimetype not in ALLOWED_FORMATS:
                context.message = ERR_NOT_ODT(filename=filename)
            template = odf_get_document(StringIO(body))
        else:
            template_name = form['template']
            if template_name:
                template_resource = parent.get_resource(template_name)
                body = template_resource.handler.to_str()
                template = odf_get_document(StringIO(body))

        book = resource.get_book()
        if book is not None:
            # Prepare document
            if template is None:
                document = odf_new_document_from_type('text')
            else:
                document = template.clone()
                document.get_body().clear()
            # Metadata
            meta = document.get_meta()
            now = datetime.now()
            meta.set_creation_date(now)
            meta.set_modification_date(now)
            meta.set_editing_duration(timedelta(0))
            meta.set_editing_cycles(1)
            meta.set_generator(u"ikaaro.wiki to ODT")
            for metadata in ('title', 'comments', 'subject', 'language',
                    'keywords'):
                getattr(meta, 'set_' + metadata)(book.get(metadata))
            # Cover page
            cover_uri = book.get('cover')
            if cover_uri:
                cover = parent.get_resource(cover_uri, soft=True)
                if cover is None:
                    if not form['ignore_missing_pages']:
                        context.message = ERR_PAGE_NOT_FOUND(uri=cover_uri)
                        return
                else:
                    doctree = cover.get_doctree()
                    resolve_references(doctree, resource, context)
                    resolve_images(doctree, resource, context)
                    heading_level = 0 if startswith_section(doctree) else 1
                    convert_context = make_context(document,
                            heading_level=heading_level,
                            skip_toc=True,
                            # Override temporarly convert_title
                            convert_title=convert_cover_title,
                            convert_subtitle=convert_cover_title)
                    convert(document, doctree, context=convert_context)
            # Global TOC
            language = book.get('language').split('-')[0]
            title = MSG(u"Table of Contents").gettext(language=language)
            outline_level = book.get('toc-depth', 10)
            toc = odf_create_toc(title=title, outline_level=outline_level)
            document.get_body().append(toc)
            # List of pages and their starting title level
            doctree = resource.get_doctree()
            visitor = PageVisitor(doctree, resource,
                    ignore_missing_pages=form['ignore_missing_pages'])
            try:
                book.walkabout(visitor)
            except LookupError, uri:
                context.message = ERR_PAGE_NOT_FOUND(uri=uri)
                return
            pages = visitor.pages
            if not pages:
                context.message = ERR_NO_PAGE_FOUND
                return
            # List of links between pages
            known_links = [ page.abspath for page, _ in pages ]
            # Compile pages
            for page, level in pages:
                doctree = page.get_doctree()
                try:
                    resolve_references(doctree, page, context,
                            reference_resolver=odt_reference_resolver,
                            known_links=known_links)
                except ValueError, e:
                    context.message = ERROR(unicode(str(e), 'utf_8'))
                    return
                resolve_images(doctree, resource, context)
                if startswith_section(doctree):
                    # convert_section will increment it
                    level -= 1
                convert_context = make_context(document,
                        heading_level=level,
                        skip_toc=True)
                convert(document, doctree, context=convert_context)
            # Fill TOC
            toc.fill()
        else:
            # Just convert the page as is to ODT
            doctree = resource.get_doctree()
            resolve_references(doctree, resource, context)
            resolve_images(doctree, resource, context)
            # convert_section will increment it
            heading_level = 0 if startswith_section(doctree) else 1
            document = rst2odt(doctree, template=template,
                    heading_level=heading_level)

        context.set_content_type('application/vnd.oasis.opendocument.text')
        filename = book.get('filename') if book else None
        if not filename:
            filename = '%s.odt' % resource.name
        context.set_content_disposition('attachment', filename=filename)

        output = StringIO()
        document.save(output)
        return output.getvalue()



class WikiPage_Help(STLView):
    access = 'is_allowed_to_view'
    title = MSG(u"Help")
    template = '/ui/wiki/help.xml'
    styles = ['/ui/wiki/style.css']


    def get_namespace(self, resource, context):
        source = resource.get_resource('/ui/wiki/help.txt')
        source = source.to_str()

        overrides = dict(resource.overrides)
        overrides['stylesheet'] = ''
        html = publish_string(source, writer_name='html',
                               settings_overrides=overrides)
        document = XHTMLFile(string=html)
        events = document.get_body().get_content_elements()

        # Now remove some magic to make the help work like a wiki page
        source = source.split('.. XXX SPLIT HERE')[0]

        return {
            'help_source': source,
            'help_html': events,
        }



class WikiPage_HelpODT(STLView):
    access = 'is_allowed_to_view'
    title = MSG(u"ODT Help")
    template = '/ui/wiki/help_odt.xml'
