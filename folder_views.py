# -*- coding: UTF-8 -*-
# Copyright (C) 2007 Henry Obein <henry@itaapy.com>
# Copyright (C) 2007 Sylvain Taverne <sylvain@itaapy.com>
# Copyright (C) 2007, 2010 Hervé Cauwelier <herve@itaapy.com>
# Copyright (C) 2007-2008 Juan David Ibáñez Palomar <jdavid@itaapy.com>
# Copyright (C) 2008 Gautier Hayoun <gautier.hayoun@itaapy.com>
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

# Import from itools
from itools.core import merge_dicts, freeze
from itools.datatypes import Unicode, LanguageTag, Integer
from itools.fs import FileName
from itools.fs.common import get_mimetype
from itools.gettext import MSG
from itools.handlers import checkid
from itools.i18n import get_language_name
from itools.web import ERROR

# Import from ikaaro
from ikaaro.datatypes import FileDataType
from ikaaro.messages import MSG_BAD_NAME
from ikaaro.popup import DBResource_AddBase, AddBase_BrowseContent
from ikaaro.popup import DBResource_AddLink, DBResource_AddImage
from ikaaro.registry import get_resource_class
from ikaaro.utils import generate_name
from ikaaro.views import ContextMenu
from page import WikiPage
from page_views import ALLOWED_FORMATS


#################################################################
# Private API
#################################################################
def generate_title_and_name(title, used):
    # The easy case
    name = checkid(title)
    if name not in used:
        return title, name

    # OK we must search for a free title/name
    index = 0
    while True:
        title2 = u'%s (%d)' % (title, index)
        name = checkid(title2)
        if name not in used:
            return title2, name
        index += 1



def _add_image(filename, document, resource):
    if type(filename) is unicode:
        filename = filename.encode('UTF-8')
    data = document.get_part('Pictures/%s' % filename)
    name, a_type, language = FileName.decode(filename)

    # Check the filename is good
    name = checkid(name)
    if name is None:
        return None

    # XXX If the resource exists, we assume it's the good resource
    if resource.get_resource(name, soft=True) is None:
        # Get mimetype / class
        mimetype = get_mimetype(filename)
        cls = get_resource_class(mimetype)
        # Add the image
        resource.make_resource(name, cls, body=data, format=mimetype,
                               filename=filename, extension=a_type)
    # All OK
    return name



def _convert_images(content, document, resource):
    result = []
    template = '.. image:: Pictures/'
    for line in content.splitlines():
        if line.startswith(template):
            # Compute the filename (suppress the template)
            filename = line[20:]
            # Add the image and compute its name
            name = _add_image(filename, document, resource)
            if name is None:
                continue
            # And modify the page
            result.append('.. figure:: {name}'.format(name=name))
        else:
            result.append(line)

    return '\r\n'.join(result)



def _insert_notes_and_co(lpod_context, content, document, resource):

    # Insert the notes
    footnotes = lpod_context['footnotes']
    if footnotes:
        content.append(u'\n')
        for citation, body in footnotes:
            content.append(u'.. [#] %s\n' % body)
        # Append a \n after the notes
        content.append(u'\n')
        # Reset
        lpod_context['footnotes'] = []

    # Insert the annotations
    annotations = lpod_context['annotations']
    if annotations:
        content.append(u'\n')
        for annotation in annotations:
            content.append('.. [#] %s\n' % annotation)
        # Reset
        lpod_context['annotations'] = []

    # Insert the images ref
    images = lpod_context['images']
    if images:
        content.append(u'\n')
        for ref, filename, (width, height) in images:
            # Delete 'Pictures/'
            filename = filename[9:]
            name = _add_image(filename, document, resource)
            if name is None:
                continue
            content.append(u'.. %s image:: %s\n' % (ref, name))
            if width is not None:
                content.append(u'   :width: %s\n' % width)
            if height is not None:
                content.append(u'   :height: %s\n' % height)
            content.append(u'\n')
        lpod_context['images'] = []



def _insert_endnotes(lpod_context, content):

    # Insert the end notes
    endnotes = lpod_context['endnotes']
    if endnotes:
        content.append(u'\n\n')
        for citation, body in endnotes:
            content.append(u'.. [*] %s\n' % body)
        # Reset
        lpod_context['endnotes'] = []



def _format_meta(form, template_name, toc_depth, language, document):
    """Format the metadata of a rst book from a lpod document.
    """
    content = []
    content.append(u'   :toc-depth: %s' % toc_depth)
    content.append(u'   :template: %s' % template_name)
    content.append(u'   :ignore-missing-pages: no')
    for key in ['title', 'comments', 'subject', 'keywords']:
        content.append(u'   :%s: %s' % (key,  form[key]))
    content.append(u'   :language: %s' % language)

    # Compute a default filename
    title = document.get_part('meta').get_title()
    if title is None or not title.strip():
        filename, _, _ = form['file']
        filename = checkid(filename)
    else:
        filename = checkid(title.strip()) + '.odt'
    content.append(u'   :filename: %s' % filename)

    content.append(u'')
    return u"\n".join(content)



def _get_cover_title_and_name(resource, document, template_name):

    # Compute an explicit name
    title = document.get_part('meta').get_title()
    if not title:
        title = template_name
    if title:
        title = u'Cover "%s"' % title
    else:
        title = u'Cover'

    return generate_title_and_name(title, resource.get_names())



def _add_wiki_page(resource, name, title, content):
    """Add a WikiPage in resource, with title and content set.
    """
    resource.make_resource(name, WikiPage, body=content, title={'en': title})



def _format_content(resource, document, template_name, max_allowed_level):
    """Format the content of a rst book from a lpod document.
    """

    # Get the body
    body = document.get_body()

    # Create a context for the lpod functions
    lpod_context = {
        'document': document,
        'footnotes': [],
        'endnotes': [],
        'annotations': [],
        'rst_mode': True,
        'img_counter': 0,
        'images': [],
        'no_img_level': 0}

    # Main loop
    name = None
    title = u'Cover'
    links = u''
    max_level = 0
    last_level = 1
    content = []
    children = body.get_children()
    while children:
        element = children.pop(0)

        # Sections contain titles we are looking for
        if element.get_tag() == 'text:section':
            children = element.get_children() + children
            continue

        # The headings are used to split the document
        if element.get_tag() == 'text:h':
            # 0- Is this heading good ?

            # Empty tag?
            if not element.get_text() and not element.get_children():
                continue

            # Get the level
            level = element.get_outline_level()

            # If the level is too small, we don't make a new page
            if max_allowed_level and level > max_allowed_level:
                content.append(element.get_formatted_text(lpod_context))
                continue

            # 1- Save this page

            # Generate the content
            _insert_notes_and_co(lpod_context, content, document, resource)
            _insert_endnotes(lpod_context, content)
            content =  u''.join(content).encode('utf-8')
            content = _convert_images(content, document, resource)

            # In the cover ?
            if name is None:
                cover, name = _get_cover_title_and_name(resource, document,
                        template_name)

            # Add the page
            _add_wiki_page(resource, name, title, content)

            # 2- Prepare the next page

            # Update max_level
            max_level = max(level, max_level)

            # Get the title
            fake_context = dict(lpod_context)
            fake_context['rst_mode'] = False
            title = element.get_formatted_text(fake_context).strip()

            # Start a new content with the title, but without the first
            # '\n'
            content = [element.get_formatted_text(lpod_context)[1:]]

            # Search for a free WikiPage name
            if not title:
                title = u'Invalid name'
            names = resource.get_names()
            link_title, name = generate_title_and_name(title, names)

            # Update links (add eventually blank levels to avoid a problem
            # with an inconsistency use of levels in the ODT file)
            for x in range(last_level + 1, level):
                links += u'   ' * x + u'- [unknown title]\n'
            last_level = level
            links += u'   ' * level + u'- `' + link_title + u'`_\n'

        # Search for Title and Subtitle (useful for the cover).
        # We assume that these elements are direct children of the body
        elif element.get_tag() == 'text:p':
            text = element.get_formatted_text(lpod_context)

            # Search Title/Subtitle also in the hierarchy
            style = element.get_style()
            while style and style not in ('Title', 'Subtitle'):
                style = document.get_style('paragraph', style)
                style = style.get_parent_style()

            # Convert Title/Subtitle in "wiki" title
            if style and style in ('Title', 'Subtitle') and text.strip():
                text = text.replace('\n', ' ').strip()
                content.append('\n' + text + '\n')
                if style == 'Title':
                    content.append('#' * len(text) + '\n\n')
                else:
                    content.append('=' * len(text) + '\n\n')
            else:
                content.append(text)
            _insert_notes_and_co(lpod_context, content, document, resource)
        # An other element
        else:
            content.append(element.get_formatted_text(lpod_context))
            _insert_notes_and_co(lpod_context, content, document, resource)


    # 3- Save the last page

    # Generate the content
    _insert_notes_and_co(lpod_context, content, document, resource)
    _insert_endnotes(lpod_context, content)
    content =  u''.join(content).encode('utf-8')
    content = _convert_images(content, document, resource)

    # In the cover ?
    if name is None:
        cover, name = _get_cover_title_and_name(resource, document,
                template_name)

    # Add the page
    _add_wiki_page(resource, name, title, content)

    return cover, links, max_level



def _save_template(context, a_file, target_path):
    """Save the imported template.
    """
    filename, mimetype, body = a_file
    name, type, language = FileName.decode(filename)
    # Check the filename is good
    name = checkid(name)
    if name is None:
        context.message = MSG_BAD_NAME
        return

    # Get the container
    container = context.root.get_resource(target_path)

    # Search for a free name
    names = container.get_names()
    name = generate_name(name, names)

    # Add the image to the resource
    cls = get_resource_class(mimetype)
    container.make_resource(name, cls, body=body, format=mimetype,
            filename=filename, extension=type)

    return name



#################################################################
# Public API
#################################################################

class WikiMenu(ContextMenu):

    title = MSG(u'Wiki')

    def get_items(self):
        context = self.context
        resource = self.resource
        # If called from a child
        if isinstance(resource, WikiPage):
            resource = resource.parent
        # Namespace
        base = context.get_link(resource)
        return [
            {'title': view.title,
             'href': '%s/;%s' % (base, name)}
            for name, view in resource.get_views() ]



class WikiFolder_AddBase(DBResource_AddBase):

    def get_scripts(self, context):
        mode = context.get_form_value('mode')
        if mode == 'wiki':
            return ['/ui/wiki/javascript.js']
        return []



class WikiFolder_AddLink(WikiFolder_AddBase, DBResource_AddLink):

    def get_page_type(self, mode):
        """Return the type of page to add corresponding to the mode
        """
        if mode == 'wiki':
            from page import WikiPage
            return WikiPage
        raise ValueError, 'Incorrect mode %s' % mode


class WikiFolder_AddImage(WikiFolder_AddBase, DBResource_AddImage):
    pass



class WikiFolder_ImportODT(WikiFolder_AddBase):
    template = '/ui/wiki/importodt.xml'
    element_to_add = 'odt'
    browse_content_class = AddBase_BrowseContent

    schema = freeze(merge_dicts(
        DBResource_AddBase.schema,
        title=Unicode,
        subject=Unicode,
        keywords=Unicode,
        comments=Unicode,
        language=LanguageTag(default=('en', 'EN')),
        max_level=Integer))

    action_upload_schema = freeze(merge_dicts(
        schema,
        file=FileDataType(mandatory=True)))

    text_values = freeze({
        'upload': MSG(u"Upload an ODT to import it:"),
        'method': ';import_odt'})


    def get_item_classes(self):
        from ikaaro.file import ODT
        return self.item_classes if self.item_classes else (ODT,)


    def get_configuration(self):
        return {
            'show_browse': False,
            'show_external': False,
            'show_insert': False,
            'show_upload': True}


    def get_namespace(self, resource, context):
        proxy = super(WikiFolder_ImportODT, self)
        namespace = proxy.get_namespace(resource, context)
        root = resource.get_site_root()
        languages = root.get_property('website_languages')

        namespace['meta-lang'] = []
        for code in languages:
            namespace['meta-lang'].append({
                'name': get_language_name(code),
                'value': code})
        return namespace


    def get_language(self, language):
        """Format appropriate language code.
        """
        # Special case for languages
        if language:
            language, locality = language
            if locality:
                return '%s-%s' % (language, locality)
            else:
                return language
        else:
            return self.get_site_root().get_default_language()


    def do_import(self, resource, data, form, template_name):
        """Format the content of a rst book and create related resources.
        """

        # Get the document
        from lpod.document import odf_get_document
        document = odf_get_document(StringIO(data))

        # Auto clean the document
        from lpod.cleaner import clean_document
        document, _ = clean_document(document)

        # Make the book
        cover, links, toc_depth = _format_content(resource, document,
                template_name, form['max_level'])
        language = self.get_language(form['language'])
        meta = _format_meta(form, template_name, toc_depth, language,
                document)
        return u' `%s`_\n%s\n%s' % (cover, meta, links)


    def action_upload(self, resource, context, form):
        """Insert a wikibook directly. The uploaded document is saved.
        """
        # Check the mimetype
        a_file = form['file']
        filename, mimetype, data = a_file
        if mimetype not in ALLOWED_FORMATS:
            context.message = ERROR(u'"%s" is not an OpenDocument Text' %
                    filename)
            return

        # Save the file
        target_path = form['target_path']
        template_name = _save_template(context, a_file, target_path)
        if template_name is None:
            return

        # Return javascript
        scripts = self.get_scripts(context)
        context.add_script(*scripts)

        # Build RST Book
        book = self.do_import(resource, data, form, template_name)
        # Escape characters for JavaScript
        book = book.encode('utf-8')
        book = book.replace("\n", "\\n").replace("'", "\\'")
        return self.get_javascript_return(context, book)
