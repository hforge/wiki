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

# Import from itools
from itools.gettext import MSG

# Import from ikaaro
from ikaaro.autoedit import AutoEdit
from ikaaro.file import File
from ikaaro.folder import Folder
from ikaaro.folder_views import GoToSpecificDocument
from ikaaro.registry import register_document_type

# Import from wiki
from folder_views import WikiMenu, WikiFolder_ImportODT
from folder_views import WikiFolder_AddLink, WikiFolder_AddImage
from page import WikiPage


class WikiFolder(Folder):
    class_id = 'WikiFolder'
    class_version = '20071215'
    class_title = MSG(u"Wiki")
    class_description = MSG(u"Container for a wiki")
    class_icon16 = 'wiki/WikiFolder16.png'
    class_icon48 = 'wiki/WikiFolder48.png'
    class_views = ['view', 'browse_content', 'preview_content', 'edit',
                   'new_resource', 'orphans', 'commit_log']

    __fixed_handlers__ = ['FrontPage']

    # User Interface
    context_menus = [WikiMenu()]

    # Hide itws sidebar
    display_sidebar = False

    # Views
    view = GoToSpecificDocument(specific_document='FrontPage')
    edit = AutoEdit(title=MSG(u"Edit Wiki"))
    add_link = WikiFolder_AddLink()
    add_image = WikiFolder_AddImage()
    import_odt = WikiFolder_ImportODT()

    # Wiki
    default_page = 'FrontPage'


    #############################################
    # Folder API
    #############################################
    def init_resource(self, **kw):
        Folder.init_resource(self, **kw)
        # FrontPage
        self.make_resource(self.default_page, WikiPage,
                title={'en': u"Front Page"})


    def get_document_types(self):
        return [WikiPage, File]



# Register document type
register_document_type(WikiFolder)
