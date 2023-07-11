# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# ##### END GPL LICENSE BLOCK #####

if "bpy" in locals():
    import importlib
    if "export_dae" in locals():
        importlib.reload(export_dae) # noqa

from pathlib import Path
import tempfile
import bpy
import bmesh
import os
import os.path
import subprocess
import xml.etree.ElementTree as et

from bpy.types import Operator, AddonPreferences, PropertyGroup, UIList, Panel
from bpy.props import StringProperty, BoolProperty, FloatProperty, EnumProperty, CollectionProperty, PointerProperty, IntProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from math import radians, degrees
from mathutils import Euler, Matrix

from . import export_dae

bl_info = {
    "name": "DOS2/BG3 Collada Exporter",
    "author": "LaughingLeader / Norbyte",
    "blender": (3, 6, 0),
    "version": (2, 0, 0),
    "location": "File > Import-Export",
    "description": ("Export Collada/Granny files for Divinity Original Sin / Baldur's Gate 3."),
    "warning": "",
    "doc_url": "",
    "tracker_url": "",
    "support": "COMMUNITY",
    "category": "Import-Export"
}

gr2_extra_flags = (
    ("DISABLED", "Disabled", ""),
    ("MESHPROXY", "MeshProxy", "Flags the mesh as a meshproxy, used for displaying overlay effects on a weapon and AllSpark MeshEmiters"),
    ("CLOTH", "Cloth", "The mesh has vertex painting for use with Divinity's cloth system"),
    ("RIGID", "Rigid", "For meshes lacking an armature modifier. Typically used for weapons"),
    ("RIGIDCLOTH", "Rigid&Cloth", "For meshes lacking an armature modifier that also contain cloth physics. Typically used for weapons")
)

game_versions = (
    ("dos", "DOS", "Divinity: Original Sin"),
    ("dosee", "DOS: EE", "Divinity: Original Sin - Enhanced Edition"),
    ("dos2", "DOS 2", "Divinity: Original Sin 2"),
    ("dos2de", "DOS 2: DE", "Divinity: Original Sin 2 - Definitive Edition"),
    ("bg3", "BG 3", "Baldur's Gate 3"),
    ("unset", "Unset", "Unset")
)

current_operator = None

def report(msg, reportType="WARNING"):
    if current_operator is not None:
        current_operator.report(set((reportType, )), msg)
    print("{} ({})".format(msg, reportType))

def get_prefs(context):
    return context.preferences.addons["io_scene_dos2de"].preferences

class ProjectData(PropertyGroup):
    project_folder: StringProperty(
        name="Project Folder",
        description="The root folder where .blend files are stored"
    )
    export_folder: StringProperty(
        name="Export Folder",
        description="The root export folder"
    )

class ProjectEntry(PropertyGroup):
    project_data: CollectionProperty(type=ProjectData)
    index: IntProperty()

class DIVINITYEXPORTER_OT_add_project(Operator):
    bl_idname = "divinityexporter.add_project"
    bl_label = "Add Project"
    bl_description = "Add an entry to the project list"

    def execute(self, context):
        get_prefs(context).projects.project_data.add()
        return {'FINISHED'}

class DIVINITYEXPORTER_OT_remove_project(Operator):
    bl_idname = "divinityexporter.remove_project"
    bl_label = "Remove"
    bl_description = "Remove Project"

    selected_project: CollectionProperty(type=ProjectData)

    def set_selected(self, item):
        selected_project = item

    def execute(self, context):
        addon_prefs = get_prefs(context)

        i = 0
        for project in addon_prefs.projects.project_data:
            if (project.project_folder == self.selected_project[0].project_folder
                and project.export_folder == self.selected_project[0].export_folder):
                    addon_prefs.projects.project_data.remove(i)
            i += 1

        self.selected_project.clear()

        return {'FINISHED'}

class DIVINITYEXPORTER_UL_project_list(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            layout.prop(item, "project_folder", text="Project Folder")
            layout.prop(item, "export_folder", text="Export Folder")
            op = layout.operator("divinityexporter.remove_project", icon="CANCEL", text="", emboss=False)
            #Is there no better way?
            project = op.selected_project.add()
            project.project_folder = item.project_folder
            project.export_folder = item.export_folder

        elif self.layout_type in {'GRID'}:
            layout.alignment = 'CENTER'
            layout.label(text="", icon_value=icon)

class DIVINITYEXPORTER_AddonPreferences(AddonPreferences):
    bl_idname = "io_scene_dos2de"

    lslib_path: StringProperty(
        name="Divine Path",
        description="The path to divine.exe, used to convert from dae to gr2",
        subtype='FILE_PATH',
    )
    gr2_default_enabled: BoolProperty(
        name="Convert to GR2 by Default",
        default=True,
        description="Models will be converted to gr2 by default if the Divine Path is set"
    )

    default_preset: EnumProperty(
        name="Default Preset",
        description="The default preset to load when the exporter is opened for the first time",
        items=(("NONE", "None", ""),
               ("MESHPROXY", "MeshProxy", "Use default meshproxy settings"),
               ("ANIMATION", "Animation", "Use default animation settings"),
               ("MODEL", "Model", "Use default model settings")),
        default=("NONE")
    )

    auto_export_subfolder: BoolProperty(
        name="Use Preset Type for Project Export Subfolder",
        description="If enabled, the export subfolder will be determined by the preset type set.\nFor instance, Models go into \Models",
        default=False
    )

    projects: PointerProperty(
        type=ProjectEntry,
        name="Projects",
        description="Project pathways to auto-detect when exporting"
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text="Divinity Export Addon Preferences")
        layout.prop(self, "lslib_path")
        layout.prop(self, "gr2_default_enabled")
        layout.prop(self, "default_preset")
        layout.prop(self, "auto_export_subfolder")

        layout.separator()
        layout.label(text="Projects")
        layout.template_list("DIVINITYEXPORTER_UL_project_list", "", self.projects, "project_data", self.projects, "index")
        layout.operator("divinityexporter.add_project")

class GR2_ExportSettings(PropertyGroup):
    """GR2 Export Options"""

    extras: EnumProperty(
        name="Flag",
        description="Flag every mesh with the selected flag.\nNote: Custom Properties on a mesh will override this",
        items=gr2_extra_flags,
        default=("DISABLED")
    )
    yup_conversion: BoolProperty(
        name="Convert to Y-Up",
        default=False
    )
    apply_basis_transforms: BoolProperty(
        name="Apply Y-Up Transformations",
        default=False
    )
    force_legacy: BoolProperty(
        name="Force Legacy GR2 Version Tag",
        default=False
    )
    store_indices: BoolProperty(
        name="Store Compact Tri Indices",
        default=True
    )
    create_dummyskeleton: BoolProperty(
        name="Create Dummy Skeleton",
        default=True
    )

    def draw(self, context, obj):
        obj.label(text="GR2 Options")
        obj.prop(self, "yup_conversion")
        obj.prop(self, "apply_basis_transforms")
        obj.prop(self, "force_legacy")
        obj.prop(self, "store_indices")
        obj.prop(self, "create_dummyskeleton")

        obj.label(text="Extra Properties (Global)")
        obj.prop(self, "extras")
        #extrasobj = obj.row(align=False)
        #self.extras.draw(context, extrasobj)

class Divine_ExportSettings(PropertyGroup):
    """Divine GR2 Conversion Settings"""
    gr2_settings: bpy.props.PointerProperty(
        type=GR2_ExportSettings,
        name="GR2 Export Options"
    )

    game: EnumProperty(
        name="Game",
        description="The target game. Currently determines the model format type",
        items=game_versions,
        default=("dos2de")
    )

    delete_collada: BoolProperty(
        name="Delete Exported Collada File",
        default=True,
        description="The resulting .dae file will be deleted after being converted to gr2"
    )

    xflip_skeletons: BoolProperty(
        name="X-Flip Skeletons",
        default=False
    )
    xflip_meshes: BoolProperty(
        name="X-Flip Meshes",
        default=False
    )

    flip_uvs: BoolProperty(
        name="Flip UVs",
        default=True
    )
    filter_uvs: BoolProperty(
        name="Filter UVs",
        default=False
    )
    ignore_uv_nan: BoolProperty(
        name="Ignore Bad NaN UVs",
        description="Ignore bad/unwrapped UVs that fail to form a triangle. Export will fail if these are detected",
        default=False
    )
    export_normals: BoolProperty(
        name="Export Normals",
        default=True
    )
    export_tangents: BoolProperty(
        name="Export Tangent/Bitangent",
        default=True
    )
    export_uvs: BoolProperty(
        name="Export UVs",
        default=True
    )
    export_colors: BoolProperty(
        name="Export Colors",
        default=True
    )
    deduplicate_vertices: BoolProperty(
        name="Deduplicate Vertices",
        default=True
    )
    deduplicate_uvs: BoolProperty(
        name="Deduplicate UVs",
        default=True
    )
    recalculate_normals: BoolProperty(
        name="Recalculate Normals",
        default=False
    )
    recalculate_tangents: BoolProperty(
        name="Recalculate Tangent/Bitangent",
        default=False
    )
    recalculate_iwt: BoolProperty(
        name="Recalculate Inverse World Transforms",
        default=False
    )

    keep_bind_info: BoolProperty(
		name="Keep Bind Info",
		description="Store Bindpose information in custom bone properties for later use during Collada export",
		default=True)

    navigate_to_blendfolder: BoolProperty(default=False)

    drawable_props = [
        "xflip_skeletons",
        "xflip_meshes",
        "flip_uvs",
        "filter_uvs",
        "ignore_uv_nan",
        "export_normals",
        "export_tangents",
        "export_uvs",
        "export_colors",
        "deduplicate_vertices",
        "deduplicate_uvs",
        "recalculate_normals",
        "recalculate_tangents",
        "recalculate_iwt"
    ]


    def draw(self, context, obj):
        obj.prop(self, "game")
        obj.label(text="GR2 Export Settings")
        gr2box = obj.box()
        self.gr2_settings.draw(context, gr2box)

        #col = obj.column(align=True)
        obj.label(text="Export Options")
        for prop in self.drawable_props:
            obj.prop(self, prop)

class DivineInvoker:
    def __init__(self, addon_prefs, divine_prefs):
        self.addon_prefs = addon_prefs
        self.divine_prefs = divine_prefs

    def check_lslib(self):
        if self.addon_prefs.lslib_path is None or self.addon_prefs.lslib_path == "":
            report("LSLib path was not set up in addon preferences. Cannot convert to GR2.", "ERROR")
            return False
            
        lslib_path = Path(self.addon_prefs.lslib_path)
        if not lslib_path.is_file():
            report("The LSLib path set in addon preferences is invalid. Cannot convert to GR2.", "ERROR")
            return False
        
        return True

    def build_gr2_options(self):
        export_str = ""
        # Possible args:
        #"export-normals;export-tangents;export-uvs;export-colors;deduplicate-vertices;
        # deduplicate-uvs;recalculate-normals;recalculate-tangents;recalculate-iwt;flip-uvs;
        # force-legacy-version;compact-tris;build-dummy-skeleton;apply-basis-transforms;conform"

        divine_args = {
            "xflip_skeletons"           : "x-flip-skeletons",
            "xflip_meshes"              : "x-flip-meshes",
            "export_normals"            : "export-normals",
            "export_tangents"           : "export-tangents",
            "export_uvs"                : "export-uvs",
            "export_colors"             : "export-colors",
            "deduplicate_vertices"      : "deduplicate-vertices",
            "deduplicate_uvs"           : "deduplicate-uvs",
            "recalculate_normals"       : "recalculate-normals",
            "recalculate_tangents"      : "recalculate-tangents",
            "recalculate_iwt"           : "recalculate-iwt",
            "flip_uvs"                  : "flip-uvs",
            "ignore_uv_nan"             : "ignore-uv-nan"
        }

        gr2_args = {
            "force_legacy"              : "force-legacy-version",
            "store_indices"             : "compact-tris",
            "create_dummyskeleton"      : "build-dummy-skeleton",
            "yup_conversion"            : "y-up-skeletons",
            "apply_basis_transforms"    : "apply-basis-transforms"
            #"conform"					: "conform"
        }

        for prop,arg in divine_args.items():
            val = getattr(self.divine_prefs, prop, False)
            if val == True:
                export_str += "-e " + arg + " "

        gr2_settings = self.divine_prefs.gr2_settings

        for prop,arg in gr2_args.items():
            val = getattr(gr2_settings, prop, False)
            if val == True:
                export_str += "-e " + arg + " "

        return export_str

    def dae_to_gr2(self, collada_path):
        if not self.check_lslib():
            return False
        gr2_path = str.replace(collada_path, ".dae", ".gr2")
        gr2_options_str = self.build_gr2_options()
        divine_exe = '"{}"'.format(self.addon_prefs.lslib_path)
        game_ver = bpy.context.scene.ls_properties.game
        proccess_args = "{} --loglevel all -g {} -s {} -d {} -i dae -o gr2 -a convert-model {}".format(
            divine_exe, game_ver, '"{}"'.format(collada_path), '"{}"'.format(gr2_path), gr2_options_str
        )
        
        print("[DOS2DE-Collada] Starting GR2 conversion using divine.exe.")
        print("[DOS2DE-Collada] Sending command: {}".format(proccess_args))

        process = subprocess.run(proccess_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

        print(process.stdout)
        print(process.stderr)
        
        if process.returncode != 0:
            error_message = "Failed to convert Collada to GR2. {}".format(
                '\n'.join(process.stdout.splitlines()[-1:]) + '\n' + process.stderr)
            report(error_message, "ERROR")
        else:
            if self.divine_prefs.delete_collada and os.path.isfile(collada_path):
                print("[DOS2DE-Collada] GR2 conversion successful. Deleting temporary collada file '{}'.".format(collada_path))
                os.remove(collada_path)

    def gr2_to_dae(self, gr2_path, collada_path):
        if not self.check_lslib():
            return False
        divine_exe = '"{}"'.format(self.addon_prefs.lslib_path)
        proccess_args = "{} --loglevel all -g bg3 -s {} -d {} -i gr2 -o dae -a convert-model -e flip-uvs".format(
            divine_exe, '"{}"'.format(gr2_path), '"{}"'.format(collada_path)
        )
        
        print("[DOS2DE-Collada] Starting DAE conversion using divine.exe.")
        print("[DOS2DE-Collada] Sending command: {}".format(proccess_args))

        process = subprocess.run(proccess_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

        print(process.stdout)
        print(process.stderr)
        
        if process.returncode != 0:
            error_message = "Failed to convert GR2 to Collada. {}".format(
                '\n'.join(process.stdout.splitlines()[-1:]) + '\n' + process.stderr)
            report(error_message, "ERROR")
            return False
        else:
            return True


class DIVINITYEXPORTER_OT_export_collada(Operator, ExportHelper):
    """Export to Collada with Divinity-specific options (.dae)"""
    bl_idname = "export_scene.dos2de_collada"
    bl_label = "Export Collada/GR2"
    bl_options = {"PRESET", "REGISTER", "UNDO"}

    filename_ext: StringProperty(
        name="File Extension",
        options={"HIDDEN"},
        default=".dae"
    )

    filter_glob: StringProperty(default="*.dae;*.gr2", options={"HIDDEN"})
    
    filename: StringProperty(
        name="File Name",
        options={"HIDDEN"}
    )
    directory: StringProperty(
        name="Directory",
        options={"HIDDEN"}
    )

    export_directory: StringProperty(
        name="Project Export Directory",
        default="",
        options={"HIDDEN"}
    )

    use_metadata: BoolProperty(
        name="Use Metadata",
        default=True,
        options={"HIDDEN"}
        )

    auto_determine_path: BoolProperty(
        default=True,
        name="Auto-Path",
        description="Automatically determine the export path"
        )

    update_path: BoolProperty(
        default=False,
        options={"HIDDEN"}
        )
        
    auto_filepath: StringProperty(
        name="Auto Filepath",
        default="",
        options={"HIDDEN"}
        )     
        
    last_filepath: StringProperty(
        name="Last Filepath",
        default="",
        options={"HIDDEN"}
        )

    initialized: BoolProperty(default=False)
    update_path_next: BoolProperty(default=False)
    log_message: StringProperty(options={"HIDDEN"})

    gr2_default_enabled_ignore: BoolProperty(default=False, options={"HIDDEN"})

    def update_filepath(self, context):
        if self.directory == "":
            self.directory = os.path.dirname(bpy.data.filepath)

        # if self.convert_gr2:
        #     self.filename_ext = ".gr2"
        # else:
        #     self.filename_ext = ".dae"

        if self.filepath == "":
            #self.filepath = bpy.path.ensure_ext(str.replace(bpy.path.basename(bpy.data.filepath), ".blend", ""), self.filename_ext)
            self.filepath = bpy.path.ensure_ext("{}\\{}".format(self.directory, str.replace(bpy.path.basename(bpy.data.filepath), ".blend", "")), self.filename_ext)

        if self.filepath != "" and self.last_filepath == "":
            self.last_filepath = self.filepath

        next_path = ""

        if self.filepath != "":
            if self.auto_name == "LAYER":
                if "namedlayers" in bpy.data.scenes[context.scene.name]:
                    namedlayers = getattr(bpy.data.scenes[context.scene.name], "namedlayers", None)
                    if namedlayers is not None:
                        #print("ACTIVE_LAYER: {}".format(context.scene.active_layer))
                        if (bpy.data.scenes[context.scene.name].layers[context.scene.active_layer]):
                                next_path = namedlayers.layers[context.scene.active_layer].name
                else:
                    self.log_message = "The 3D Layer Manager addon must be enabled before you can use layer names when exporting."
            elif self.auto_name == "ACTION":
                armature = None
                if self.use_active_layers:
                    obj = next(iter([x for x in context.scene.objects if x.type == "ARMATURE" and x.layers[context.scene.active_layer]]))
                    if obj is not None:
                        armature = obj
                elif self.use_export_selected:
                    for obj in context.scene.objects:
                        if obj.select_get() and obj.type == "ARMATURE":
                            armature = obj
                            break
                else:
                    for obj in context.scene.objects:
                        if obj.type == "ARMATURE":
                            armature = obj
                            break
                if armature is not None:
                    anim_name = (armature.animation_data.action.name
                            if armature.animation_data is not None and
                            armature.animation_data.action is not None
                            else "")
                    if anim_name != "":
                        next_path = anim_name
                    else:
                        #Blend name
                        next_path = str.replace(bpy.path.basename(bpy.data.filepath), ".blend", "")
            elif self.auto_name == "DISABLED" and self.last_filepath != "":
                self.auto_filepath = self.last_filepath

        if self.auto_determine_path == True and get_prefs(context).auto_export_subfolder == True and self.export_directory != "":
            auto_directory = self.export_directory
            if self.selected_preset != "NONE":
                if self.selected_preset == "MODEL":
                    if "_FX_" in next_path and os.path.exists("{}\\Models\\Effects".format(self.export_directory)):
                        auto_directory = "{}\\Models\\Effects".format(self.export_directory)
                    else:
                        auto_directory = "{}\\{}".format(self.export_directory, "Models")
                elif self.selected_preset == "ANIMATION":
                    auto_directory = "{}\\{}".format(self.export_directory, "Animations")
                elif self.selected_preset == "MESHPROXY":
                    auto_directory = "{}\\{}".format(self.export_directory, "Proxy")
            
            if not os.path.exists(auto_directory):
                os.mkdir(auto_directory)
            self.directory = auto_directory
            self.update_path = True
        
        #print("Dir export_directory({}) self.directory({})".format(self.export_directory, self.directory))

        if next_path != "":
            if self.selected_preset == "MESHPROXY":
                next_path = "Proxy_{}".format(next_path)
            self.auto_filepath = bpy.path.ensure_ext("{}\\{}".format(self.directory, next_path), self.filename_ext)
            self.update_path = True

        return
 
    misc_settings_visible: BoolProperty(
        name="Misc Settings",
        default=False,
        options={"HIDDEN"}
    )

    convert_gr2: BoolProperty(
        name="Convert to GR2",
        default=False
    )

    extra_data_disabled: BoolProperty(
        name="Disable Extra Data",
        default=False
    )

    convert_gr2_options_visible: BoolProperty(
        name="GR2 Options",
        default=False,
        options={"HIDDEN"}
    )

    divine_settings: bpy.props.PointerProperty(
        type=Divine_ExportSettings,
        name="GR2 Settings"
    )

    # List of operator properties, the attributes will be assigned
    # to the class instance from the operator settings before calling
    object_types: EnumProperty(
        name="Object Types",
        options={"ENUM_FLAG"},
        items=(
               ("ARMATURE", "Armature", ""),
               ("MESH", "Mesh", ""),
               ("MATERIAL", "Material", "Export the material for each mesh"),
               ("CURVE", "Curve", ""),
               ("EMPTY", "Empty", ""),
        ),
        default={"ARMATURE", "MESH", "MATERIAL", "EMPTY", "MESH", "CURVE"}
    )

    use_export_selected: BoolProperty(
        name="Selected Only",
        description="Export only selected objects (and visible in active "
                    "layers if that applies)",
        default=False
        )

    use_export_visible: BoolProperty(
        name="Visible Only",
        description="Export only visible, unhidden, selectable objects",
        default=True
    )

    yup_rotation_options = (
        ("DISABLED", "Disabled", ""),
        ("ROTATE", "Rotate", "Rotate the object towards y-up"),
        ("ACTION", "Flag", "Flag the object as being y-up without rotating it")
    )

    xflip_armature: BoolProperty(
        name="X-Flip Armature",
        description="Flips the armature on the x-axis",
        default=False
        )
    xflip_mesh: BoolProperty(
        name="X-Flip Mesh",
        description="Flips meshes on the x-axis (DOS2/granny x-flips everything for some reason)",
        default=False
        )
    auto_name: EnumProperty(
        name="Auto-Name",
        description="Auto-generate a filename based on a property name",
        items=(("DISABLED", "Disabled", ""),
               ("LAYER", "Layer Name", ""),
               ("ACTION", "Action Name", "")),
        default=("DISABLED"),
        update=update_filepath
        )
    use_mesh_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Apply modifiers to mesh objects (on a copy!)",
        default=True
        )
    use_exclude_armature_modifier: BoolProperty(
        name="Exclude Armature Modifier",
        description="Exclude the armature modifier when applying modifiers "
                      "(otherwise animation will be applied on top of the last pose)",
        default=True
        )
    use_normalize_vert_groups: BoolProperty(
        name="Normalize Vertex Groups",
        description="Normalize all vertex groups",
        default=True
        )
    use_limit_total: BoolProperty(
        name="Limit Total",
        description="Limit total vertex influences to 4 (GR2 requirement)",
        default=True
        )
    use_rest_pose: BoolProperty(
        name="Use Rest Pose",
        description="Revert any armatures to their rest poses when exporting (on the copy only)",
        default=True
        )
    use_tangent: BoolProperty(
        name="Export Tangents",
        description="Export Tangent and Binormal arrays (for normalmapping)",
        default=True
        )
    use_triangles: BoolProperty(
        name="Triangulate",
        description="Export Triangles instead of Polygons",
        default=True
        )

    use_active_layers: BoolProperty(
        name="Active Layers Only",
        description="Export only objects on the active layers",
        default=True
        )
    use_exclude_ctrl_bones: BoolProperty(
        name="Exclude Control Bones",
        description=("Exclude skeleton bones with names beginning with 'ctrl' "
                     "or bones which are not marked as Deform bones"),
        default=False
        )
    use_anim: BoolProperty(
        name="Export Animation",
        description="Export keyframe animation",
        default=False
        )
    anim_export_all_separate: BoolProperty(
        name="Export All Actions",
        description="Export all actions as separate animation files",
        default=False
        )
    use_anim_action_all = BoolProperty(name="All Actions",
        description=("Export all actions for the first armature found in separate DAE files"),
        default=False
        )
    use_anim_skip_noexp: BoolProperty(
        name="Skip (-noexp) Actions",
        description="Skip exporting of actions whose name end in (-noexp)"
                    " Useful to skip control animations",
        default=False
        )
    use_anim_optimize: BoolProperty(
        name="Optimize Keyframes",
        description="Remove double keyframes",
        default=False
        )

    use_shape_key_export: BoolProperty(
        name="Export Shape Keys",
        description="Export shape keys for selected objects",
        default=False
        )
        
    anim_optimize_precision: FloatProperty(
        name="Precision",
        description=("Tolerence for comparing double keyframes "
                     "(higher for greater accuracy)"),
        min=1, max=16,
        soft_min=1, soft_max=16,
        default=16.0
        )

    applying_preset: BoolProperty(default=False)
    yup_local_override: BoolProperty(default=False)

    def yup_local_override_save(self, context):
        if self.applying_preset is not True:
            self.yup_local_override = True
            bpy.context.scene['dos2de_yup_local_override'] = self.yup_enabled

    yup_enabled: EnumProperty(
        name="Y-Up",
        description="Converts from Z-up to Y-up",
        items=yup_rotation_options,
        default=("DISABLED"),
        update=yup_local_override_save
        )

    # Used to reset the global extra flag when a preset is changed
    preset_applied_extra_flag: BoolProperty(default=False)
    preset_last_extra_flag: EnumProperty(items=gr2_extra_flags, default=("DISABLED"))
       
    def apply_preset(self, context):
        if self.initialized:
            #bpy.data.window_managers['dos2de_lastpreset'] = str(self.selected_preset)
            bpy.context.scene['dos2de_lastpreset'] = self.selected_preset
            self.applying_preset = True

        if self.selected_preset == "NONE":
            if self.preset_applied_extra_flag:
                if self.preset_last_extra_flag != "DISABLED":
                    self.divine_settings.gr2_settings.extras = self.preset_last_extra_flag
                    self.preset_last_extra_flag = "DISABLED"
                    print("Reverted extras flag to {}".format(self.divine_settings.gr2_settings.extras))
                else:
                    self.divine_settings.gr2_settings.extras = "DISABLED"
                self.preset_applied_extra_flag = False
            return
        elif self.selected_preset == "MODEL":
            self.object_types = {"ARMATURE", "MESH"}

            if self.yup_local_override is False:
                self.yup_enabled = "ROTATE"
            self.use_normalize_vert_groups = True
            #self.use_rest_pose = True
            self.use_triangles = True
            self.use_active_layers = True
            self.auto_name = "LAYER"

            #self.xflip_armature = False
            #self.xflip_mesh = False
            self.use_exclude_ctrl_bones = False
            self.use_anim = False
            self.use_anim_skip_noexp = False
            self.use_anim_optimize = False
            self.use_shape_key_export = False

            if self.preset_applied_extra_flag:
                if self.preset_last_extra_flag != "DISABLED":
                    self.divine_settings.gr2_settings.extras = self.preset_last_extra_flag
                    self.preset_last_extra_flag = "DISABLED"
                    print("Reverted extras flag to {}".format(self.divine_settings.gr2_settings.extras))
                else:
                    self.divine_settings.gr2_settings.extras = "DISABLED"
                self.preset_applied_extra_flag = False

        elif self.selected_preset == "ANIMATION":
            self.object_types = {"ARMATURE"}
            if self.yup_local_override is False:
                self.yup_enabled = "ROTATE"
            self.use_normalize_vert_groups = False
            self.use_rest_pose = False
            self.use_triangles = True
            self.use_active_layers = True
            self.auto_name = "ACTION"

            #self.xflip_armature = False
            #self.xflip_mesh = False
            self.use_exclude_ctrl_bones = False
            self.use_anim = True
            self.use_anim_skip_noexp = False
            self.use_anim_optimize = False
            self.use_shape_key_export = False

            if (self.preset_applied_extra_flag == False):
                if(self.preset_last_extra_flag == "DISABLED" and self.divine_settings.gr2_settings.extras != "DISABLED"):
                    self.preset_last_extra_flag = self.divine_settings.gr2_settings.extras
                self.preset_applied_extra_flag = True
            
            self.divine_settings.gr2_settings.extras = "DISABLED"

        elif self.selected_preset == "MESHPROXY":
            self.object_types = {"MESH"}
            if self.yup_local_override is False:
                self.yup_enabled = "ROTATE"
            self.use_normalize_vert_groups = True
            self.use_triangles = True
            self.use_active_layers = True
            self.auto_name = "LAYER"

            #self.xflip_armature = False
            #self.xflip_mesh = False
            self.use_exclude_ctrl_bones = False
            self.use_anim = False
            self.use_anim_skip_noexp = False
            self.use_anim_optimize = False
            self.use_shape_key_export = False

            if (self.preset_applied_extra_flag == False):
                if(self.preset_last_extra_flag == "DISABLED" and self.divine_settings.gr2_settings.extras != "DISABLED"):
                    self.preset_last_extra_flag = self.divine_settings.gr2_settings.extras
                self.preset_applied_extra_flag = True
            
            self.divine_settings.gr2_settings.extras = "MESHPROXY"

        if self.initialized:
            self.update_path_next = True
        return
        #self.selected_preset = "NONE"

    selected_preset: EnumProperty(
        name="Preset",
        description="Use a built-in preset",
        items=(("NONE", "None", ""),
               ("MESHPROXY", "MeshProxy", "Use default meshproxy settings"),
               ("ANIMATION", "Animation", "Use default animation settings"),
               ("MODEL", "Model", "Use default model settings")),
        default=("NONE"),
        update=apply_preset
    )

    batch_mode: BoolProperty(
        name="Batch Export",
        description="Export all active layers as separate files, or every action as separate animation files",
        default=False
    )

    debug_mode: BoolProperty(default=False, options={"HIDDEN"})

    def draw(self, context):
        layout = self.layout
        
        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(self, "object_types")

        col = layout.column(align=True)
        col.prop(self, "auto_determine_path")
        col.prop(self, "selected_preset")
        if self.debug_mode:
            col.prop(self, "batch_mode")

        box = layout.box()
        box.prop(self, "auto_name")
        box.prop(self, "yup_enabled")
       
        row1 = layout.row(align=True)
        row1col1 = row1.column(align=True)
        row1col2 = row1.column(align=True)
        row1col3 = row1.column(align=True)
       
        row2 = layout.row(align=True)
        row2col1 = row2.column(align=True)
        row2col2 = row2.column(align=True)
        row2col3 = row2.column(align=True)
       
        row3 = layout.row(align=True)
        row3col1 = row3.column(align=True)
        row3col2 = row3.column(align=True)
        row3col3 = row3.column(align=True)
       
        row4 = layout.row(align=True)
        row4col1 = row4.column(align=True)
        row4col2 = row4.column(align=True)
        row4col3 = row4.column(align=True)

        row1col1.prop(self, "use_active_layers")
        row2col1.prop(self, "use_export_visible")
        row3col1.prop(self, "use_export_selected")
        row4col1.prop(self, "use_mesh_modifiers")

        row1col2.prop(self, "use_tangent")
        row2col2.prop(self, "use_triangles")
        row3col2.prop(self, "xflip_mesh")
        row4col2.prop(self, "use_exclude_armature_modifier")

        row1col3.prop(self, "use_normalize_vert_groups")
        row2col3.prop(self, "use_limit_total")
        row3col3.prop(self, "use_rest_pose")
        row4col3.label(text="")
        #if self.use_mesh_modifiers:
        
        #col = layout.column(align=True)
        #row = col.row(align=True)
        #row.prop(self, "xflip_armature")
        #row.prop(self, "xflip_mesh")

        box = layout.box()
        box.prop(self, "use_anim")
        if self.use_anim:
            box.label(text="Animation Settings")
            if self.debug_mode:
                box.prop(self, "anim_export_all_separate")
            box.prop(self, "use_anim_skip_noexp")
            box.prop(self, "use_anim_optimize")
            box.prop(self, "anim_optimize_precision")

        box = layout.box()
        box.prop(self, "convert_gr2")

        if self.convert_gr2:
            box.prop(self.divine_settings, "delete_collada")
            label = "Show GR2 Options" if not self.convert_gr2_options_visible else "Hide GR2 Options"
            box.prop(self, "convert_gr2_options_visible", text=label, toggle=True)

            if self.convert_gr2_options_visible:
                self.divine_settings.draw(context, box)

        col = layout.column(align=True)
        label = "Misc Settings" if not self.convert_gr2_options_visible else "Misc Settings"
        col.prop(self, "misc_settings_visible", text=label, toggle=True)
        if self.misc_settings_visible:
            box = layout.box()
            box.prop(self, "use_exclude_ctrl_bones")
            box.prop(self, "use_shape_key_export")
            
    @property
    def check_extension(self):
        return True
    
    def check(self, context):
        self.applying_preset = False

        if self.log_message != "":
            print(self.log_message)
            report("{}".format(self.log_message), "WARNING")
            self.log_message = ""

        if self.convert_gr2 == False:
            self.gr2_default_enabled_ignore = True
        elif self.gr2_default_enabled_ignore == True:
            self.gr2_default_enabled_ignore = False

        update = False

        if self.divine_settings.navigate_to_blendfolder == True:
            self.directory = os.path.dirname(bpy.data.filepath)
            self.filepath = "" #reset
            self.update_path_next = True
            self.divine_settings.navigate_to_blendfolder = False

        if(self.update_path_next):
            self.update_filepath(context)
            self.update_path_next = False
        
        if self.update_path:
            update = True
            self.update_path = False
            if self.filepath != self.auto_filepath:
                self.filepath = bpy.path.ensure_ext(self.auto_filepath, self.filename_ext)
                #print("[DOS2DE] Filepath is actually: " + self.filepath)

        return update
        
    def invoke(self, context, event):
        addon_prefs = get_prefs(context)

        blend_path = bpy.data.filepath
        #print("Blend path: {} ".format(blend_path))

        if addon_prefs.gr2_default_enabled == True and self.gr2_default_enabled_ignore == False:
            self.convert_gr2 = True

        saved_preset = bpy.context.scene.get('dos2de_lastpreset', None)

        if saved_preset is not None:
            self.selected_preset = saved_preset
        else:
            if addon_prefs.default_preset != "NONE":
                self.selected_preset = addon_prefs.default_preset

        if "laughingleader_blender_helpers" in context.preferences.addons:
            helper_preferences = context.preferences.addons["laughingleader_blender_helpers"].preferences
            if helper_preferences is not None:
                self.debug_mode = getattr(helper_preferences, "debug_mode", False)
        #print("Preset: \"{}\"".format(self.selected_preset))

        # Multiple meshes tend to need different materials for programs like Substance Painter
        if self.selected_preset == "MODEL" and self.convert_gr2 == False:
            num_meshes = 0

            if self.use_active_layers:
                for i in range(20):
                    if context.scene.layers[i]:
                        for obj in context.scene.objects:
                            if obj.layers[i] and obj.type == "MESH":
                                num_meshes += 1
            elif self.use_export_selected:
                for obj in context.scene.objects:
                    if obj.select_get() and obj.type == "MESH":
                        num_meshes += 1
            else:
                for obj in context.scene.objects:
                    if obj.type == "MESH":
                        num_meshes += 1
            
            print("Total Meshes: \"{}\"".format(num_meshes))

            if num_meshes > 1:
                self.object_types = {"ARMATURE", "MESH", "MATERIAL"}

        yup_local_override = bpy.context.scene.get('dos2de_yup_local_override', None)

        if yup_local_override is not None:
            self.yup_enabled = yup_local_override

        if self.filepath != "" and self.last_filepath == "":
            self.last_filepath = self.filepath

        if addon_prefs.projects and self.auto_determine_path == True:
            projects = addon_prefs.projects.project_data
            if projects:
                for project in projects:
                    project_folder = project.project_folder
                    export_folder = project.export_folder

                    print("Checking {} for {}".format(blend_path, project_folder))

                    if(export_folder != "" and project_folder != "" and 
                        bpy.path.is_subdir(blend_path, project_folder)):
                            self.export_directory = export_folder
                            self.directory = export_folder
                            self.filepath = export_folder
                            self.last_filepath = self.filepath
                            print("Setting start path to export folder: \"{}\"".format(export_folder))
                            break

        self.update_filepath(context)
        context.window_manager.fileselect_add(self)

        self.initialized = True

        return {'RUNNING_MODAL'}

    def can_modify_object(self, context, obj):
        if self.use_export_visible and obj.hide_get() or obj.hide_select:
            return False
        if self.use_export_selected and not obj.select_get():
            return False
        if self.use_active_layers:
            valid = True
            for col in obj.users_collection:
                if col.hide_viewport == True:
                    valid = False
                    break
                    
            if not valid:
                return False

        #print("[DOS2DE-Exporter] Obj '{}' can be exported".format(obj.name))
        return True

    def pose_apply(self, context, obj):
        last_active = getattr(bpy.context.scene.objects, "active", None)
        bpy.ops.object.select_all(action='DESELECT')
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="POSE")
        bpy.ops.pose.armature_apply()
        obj.select_set(False)
        bpy.context.view_layer.objects.active = last_active
    
    def transform_apply(self, context, obj, location=False, rotation=False, scale=False):
        last_active = getattr(bpy.context.scene.objects, "active", None)
        bpy.ops.object.select_all(action='DESELECT')
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.transform_apply(location=location, rotation=rotation, scale=scale)
        obj.select_set(False)
        bpy.context.view_layer.objects.active = last_active

    def copy_obj(self, context, obj, parent=None):
        copy = obj.copy()
        copy.use_fake_user = False

        data = getattr(obj, "data", None)
        if data != None:
            copy.data = data.copy()
            copy.data.use_fake_user = False
        
        export_props = getattr(obj, "llexportprops", None)
        if export_props is not None:
            copy.llexportprops.copy(export_props)
            copy.llexportprops.original_name = obj.name
            #copy.data.name = copy.llexportprops.export_name

        context.collection.objects.link(copy)

        print("[DOS2DE-Export] Created a copy of object/data {} ({})".format(obj.name, copy.name))

        if parent is not None:
            if self.can_modify_object(context, parent):
                copy.parent = parent
                copy.matrix_parent_inverse = obj.matrix_parent_inverse.copy()
                if parent.type == "ARMATURE":
                    armature_modifiers = (mod for mod in copy.modifiers if mod.type == "ARMATURE" and 
                        obj.parent is not None and mod.object is not None and mod.object.name == obj.parent.name)
                    for mod in armature_modifiers:
                        mod.object = parent
                        print("   [DOS2DE-Export] Updated armature modifier to point to the copied armature {} for child {}".format(parent.name, copy.name))
            else:
                report("[DOS2DE-Exporter] Object '{}' has a parent '{}' that will not export. Please unparent it or adjust the parent so it will export.".format(
                     copy.name, parent.name))

        return copy

    def cancel(self, context):
        pass

    def execute(self, context):
        global current_operator
        try:
            current_operator = self
            return self.really_execute(context)
        finally:
            current_operator = None

    def really_execute(self, context):
        if not self.filepath:
            raise Exception("filepath not set")

        result = ""
        
        addon_prefs = get_prefs(context)

        if bpy.context.object is not None and bpy.context.object.mode is not None:
            current_mode = bpy.context.object.mode
        else:
            current_mode = "OBJECT"

        activeObject = None
        if bpy.context.view_layer.objects.active:
            activeObject = bpy.context.view_layer.objects.active
        
        targetObjects = []
        modifyObjects = []
        selectedObjects = []
        copies = []

        if activeObject is not None and not activeObject.hide_get():
            bpy.ops.object.mode_set(mode="OBJECT")
        
        for obj in context.scene.objects:
            if obj.select_get():
                selectedObjects.append(obj)
                obj.select_set(False)
            
            if self.can_modify_object(context, obj):
                targetObjects.append(obj)

        for obj in targetObjects:
            parent_not_exporting = (obj.parent is not None 
                and not self.can_modify_object(context, obj.parent))
            if not obj.parent or parent_not_exporting:
                print("[DOS2DE-Exporter] Copying object '{}'.".format(obj.name))
                copy = self.copy_obj(context, obj)
                modifyObjects.append((obj, copy))
                copies.append(copy)

                if parent_not_exporting:
                    msg = "[DOS2DE-Exporter] Object '{}' has a parent '{}' that will not export. Unparenting copy and preserving transform.".format(
                        copy.name, obj.parent.name)
                    report(msg)
                    bpy.ops.object.select_all(action='DESELECT')
                    bpy.context.view_layer.objects.active = copy
                    copy.select_set(True)
                    bpy.ops.object.parent_clear(type='CLEAR_KEEP_TRANSFORM')
                    copy.select_set(False)
                    bpy.context.view_layer.objects.active = None

                for childobj in obj.children:
                    if self.can_modify_object(context, childobj):
                        childcopy = self.copy_obj(context, childobj, copy)
                        modifyObjects.append((obj, childcopy))
                        copies.append(childcopy)
            else:
                print("[DOS2DE-Exporter] object '{}' has a parent.".format(obj.name))

        for (orig, obj) in modifyObjects:
            if obj.type == "ARMATURE":
                if self.use_exclude_armature_modifier:
                    self.pose_apply(context, obj)
                elif self.use_rest_pose:
                    d = getattr(obj, "data", None)
                    if d is not None:
                        d.pose_position = "REST"
            export_props = getattr(obj, "llexportprops", None)
            if export_props is not None:
                if not obj.parent:
                    print("Preparing export properties for {}".format(obj.name))
                    export_props.prepare(context, obj)
                    for childobj in obj.children:
                        print("  Preparing export properties for child {}".format(childobj.name))
                        childobj.llexportprops.prepare(context, childobj)
                        childobj.llexportprops.prepare_name(context, childobj)
                export_props.prepare_name(context, obj)
            
            if self.yup_enabled == "ROTATE":
                if not obj.parent:
                    print("  Rotating {} to y-up. | (x={}, y={}, z={})".format(obj.name, degrees(obj.rotation_euler[0]),
                                degrees(obj.rotation_euler[1]), degrees(obj.rotation_euler[2]))
                            )
                    obj.rotation_euler = (obj.rotation_euler.to_matrix() * Matrix.Rotation(radians(-90), 3, 'X')).to_euler()
                    print("  Rotated {} to y-up. | (x={}, y={}, z={})".format(obj.name, degrees(obj.rotation_euler[0]),
                                degrees(obj.rotation_euler[1]), degrees(obj.rotation_euler[2]))
                            )
                    
                    self.transform_apply(context, obj, rotation=True)

                    for childobj in obj.children:
                        childobj.select_set(True)
                        # rot_x = degrees(childobj.rotation_euler[0])
                        # if rot_x != 0:
                        #     parent_yup_applied = round(rot_x) == -90
                        #     print("  Applying rotation transform to child {} | (x={})".format(childobj.name, rot_x))
                        #     self.transform_apply(context, childobj, rotation=True)

                        #     if parent_yup_applied == False:
                        #         print("    Rotating child to y-up: (x={}, y={}, z={})".format(degrees(childobj.rotation_euler[0]),
                        #                 degrees(childobj.rotation_euler[1]), degrees(childobj.rotation_euler[2]))
                        #             )
                        #         childobj.rotation_euler = (childobj.rotation_euler.to_matrix() * Matrix.Rotation(radians(-90), 3, 'X')).to_euler()
                        #         print("      Rotated child {} to y-up. (x={})".format(childobj.name, degrees(childobj.rotation_euler[0])))
                        #         self.transform_apply(context, childobj, rotation=True)

                    bpy.context.view_layer.objects.active = obj
                    obj.select_set(True)
                    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
                    bpy.ops.object.select_all(action='DESELECT')
                    # print(" {} Final (x={}, y={}, z={})".format(obj.name, degrees(obj.rotation_euler[0]),
                    #             degrees(obj.rotation_euler[1]), degrees(obj.rotation_euler[2]))
                    #         )
            
            if self.xflip_armature and obj.type == "ARMATURE":
                obj.scale = (-1.0, 1.0, 1.0)
                self.transform_apply(context, obj, scale=True)
                print("Flipped and applied scale transformation for {} ".format(obj.name))

            if self.xflip_mesh and obj.type == "MESH":
                self.transform_apply(context, obj, scale=True)
                obj.scale = (-1.0, 1.0, 1.0)
                self.transform_apply(context, obj, scale=True)
                #bpy.ops.object.mode_set(mode="EDIT")
                #bpy.ops.mesh.flip_normals()
                #bpy.ops.object.mode_set(mode="OBJECT")
                bm = bmesh.new()
                bm.from_mesh(obj.data)
                #bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
                bmesh.ops.reverse_faces(bm, faces=bm.faces)
                bm.to_mesh(obj.data)
                bm.clear()
                obj.data.update()
                self.transform_apply(context, obj, scale=True)
                print("Flipped and applied scale transformation for {} ".format(obj.name))
                #return {"FINISHED"}

            if self.use_mesh_modifiers and obj.type == "MESH":
                hasArmature = "ARMATURE" in self.object_types
                if orig.modifiers and len(orig.modifiers) > 0:
                    old_mesh = obj.data

                    armature_modifier = orig.modifiers.get("Armature")
                    armature_poses = [arm.pose_position for arm in bpy.data.armatures]

                    if self.use_rest_pose:
                        for arm in bpy.data.armatures:
                            arm.pose_position = "REST"

                    if self.use_exclude_armature_modifier and armature_modifier:
                        obj.modifiers.remove(armature_modifier)

                    apply_modifiers = len(obj.modifiers) > 0 and self.use_mesh_modifiers
                    if not apply_modifiers:
                        obj.modifiers.clear()

                    dg = bpy.context.evaluated_depsgraph_get()
                    mesh = obj.to_mesh(preserve_all_data_layers=True, depsgraph=dg)

                    #Reset poses
                    if armature_poses:
                        for i, arm in enumerate(bpy.data.armatures):
                            arm.pose_position = armature_poses[i]

                    obj.modifiers.clear()

                    if armature_modifier:
                        new_mod = obj.modifiers.new(armature_modifier.name, "ARMATURE")
                        new_mod.invert_vertex_group = armature_modifier.invert_vertex_group
                        new_mod.object = armature_modifier.object
                        new_mod.use_bone_envelopes = armature_modifier.use_bone_envelopes
                        new_mod.use_deform_preserve_volume = armature_modifier.use_deform_preserve_volume
                        new_mod.use_multi_modifier = armature_modifier.use_multi_modifier
                        new_mod.use_vertex_groups = armature_modifier.use_vertex_groups
                        new_mod.vertex_group = armature_modifier.vertex_group
                    
                    obj.data = mesh
                    bpy.data.meshes.remove(old_mesh)
                
                if not hasArmature and obj.parent is not None and obj.parent.type == "ARMATURE":
                    matrix_copy = obj.parent.matrix_world.copy()
                    obj.parent = None
                    obj.matrix_world = matrix_copy

            if obj.type == "MESH" and obj.vertex_groups:
                if self.use_limit_total:
                    bpy.context.view_layer.objects.active = obj
                    obj.select_set(True)
                    bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
                    bpy.ops.object.vertex_group_limit_total(limit=4)
                    bpy.ops.object.mode_set(mode="OBJECT")
                    print("Limited total vertex influences to 4 for {}.".format(obj.name))
                    obj.select_set(False)
                if self.use_normalize_vert_groups:
                    bpy.context.view_layer.objects.active = obj
                    obj.select_set(True)
                    bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
                    bpy.ops.object.vertex_group_normalize_all()
                    bpy.ops.object.mode_set(mode="OBJECT")
                    print("Normalized vertex groups for {}.".format(obj.name))
                    obj.select_set(False)

        keywords = self.as_keywords(ignore=("axis_forward",
                                            "axis_up",
                                            "global_scale",
                                            "check_existing",
                                            "filter_glob",
                                            "xna_validate",
                                            "filepath"
                                            ))

        exported_pathways = []

        single_mode = self.batch_mode == False

        if self.batch_mode:
            if self.use_anim:
                if self.anim_export_all_separate:
                    print("[DOS2DE-Exporter] Exporting all actions as separate animation files.")
                    
                    armature = next(iter(list(filter(lambda orig, obj: obj.type == "ARMATURE", modifyObjects))), None)
                    if armature is not None:
                        for action in bpy.data.actions:
                            export_name = "{}_Anim_{}".format(armature.name, action.name)
                            if self.auto_name == "ACTION":
                                export_name = action.name
                            
                            export_filepath = bpy.path.ensure_ext("{}\\{}".format(self.directory, export_name), self.filename_ext)
                            print("[DOS2DE-Exporter] Setting action to '{}' and exporting as '{}'.".format(action.name, export_filepath))
                            if armature.animation_data is None:
                                armature.animation_data_create()
                            armature.animation_data.action = action
                            if export_dae.save(self, context, [armature], filepath=export_filepath, **keywords) == {"FINISHED"}:
                                exported_pathways.append(export_filepath)
                            else:
                                report("[DOS2DE-Exporter] Failed to export '{}'.".format(export_filepath))
                    result = {"FINISHED"}
                else:
                    single_mode = True
            else:
                if self.use_active_layers:
                    progress_total = len(list(i for i in range(20) if context.scene.layers[i]))
                    for i in range(20):
                        if context.scene.layers[i]:
                            export_list = list(filter(lambda orig, obj: obj.layers[i], modifyObjects))
                            export_name = "{}_Layer{}".format(bpy.path.basename(bpy.context.blend_data.filepath), i)

                            if self.auto_name == "LAYER" and "namedlayers" in bpy.data.scenes[context.scene.name]:
                                namedlayers = getattr(bpy.data.scenes[context.scene.name], "namedlayers", None)
                                if namedlayers is not None:
                                    export_name = namedlayers.layers[i].name
                            
                            export_filepath = bpy.path.ensure_ext("{}\\{}".format(self.directory, export_name), self.filename_ext)
                            print("[DOS2DE-Exporter] Batch exporting layer '{}' as '{}'.".format(i, export_filepath))

                            if export_dae.save(self, context, export_list, filepath=export_filepath, **keywords) == {"FINISHED"}:
                                exported_pathways.append(export_filepath)
                                result = {"FINISHED"}
                            else:
                                result = {"ERROR"}
                                report( "[DOS2DE-Exporter] Failed to export '{}'.".format(export_filepath))
                else:
                    single_mode = True
        if single_mode:
            pathNoextension = os.path.splitext(self.filepath)[0]
            export_filepath = bpy.path.ensure_ext(pathNoextension, self.filename_ext)
            result = export_dae.save(self, context, copies, filepath=export_filepath, **keywords)
            if result == {"FINISHED"}:
                exported_pathways.append(export_filepath)

        bpy.ops.object.select_all(action='DESELECT')

        for obj in copies:
            if obj is not None:
                obj.select_set(True)

        bpy.ops.object.delete(use_global=True)

        #Cleanup
        for block in bpy.data.meshes:
            if block.users == 0:
                bpy.data.meshes.remove(block)

        for block in bpy.data.armatures:
            if block.users == 0:
                bpy.data.armatures.remove(block)

        for block in bpy.data.materials:
            if block.users == 0:
                bpy.data.materials.remove(block)

        for block in bpy.data.textures:
            if block.users == 0:
                bpy.data.textures.remove(block)

        for block in bpy.data.images:
            if block.users == 0:
                bpy.data.images.remove(block)

        bpy.ops.object.select_all(action='DESELECT')
        
        for obj in selectedObjects:
            obj.select_set(True)
        
        if activeObject is not None:
            bpy.context.view_layer.objects.active = activeObject
        
        # Return to previous mode
        try:
            if current_mode is not None and activeObject is not None and not activeObject.hide_get():
                if activeObject.type != "ARMATURE" and current_mode == "POSE":
                    bpy.ops.object.mode_set(mode="OBJECT")
                else:
                    bpy.ops.object.mode_set (mode=current_mode)
        except Exception as e:
            print("[DOS2DE-Collada] Error setting viewport mode:\n{}".format(e))

        if self.convert_gr2:
            divine = DivineInvoker(addon_prefs, self.divine_settings)
            for collada_file in exported_pathways:
                divine.dae_to_gr2(collada_file)

        report("Export completed successfully.", "INFO")
        return result

addon_keymaps = []

added_export_options = False

class LSMeshProperties(PropertyGroup):
    rigid: BoolProperty(
        name="Rigid",
        default = False
        )
    cloth: BoolProperty(
        name="Cloth",
        default = False
        )
    mesh_proxy: BoolProperty(
        name="Mesh Proxy",
        default = False
        )
    proxy: BoolProperty(
        name="Proxy Geometry",
        default = False
        )
    spring: BoolProperty(
        name="Spring",
        default = False
        )
    occluder: BoolProperty(
        name="Occluder",
        default = False
        )
    impostor: BoolProperty(
        name="Impostor",
        default = False
        )
    lod: IntProperty(
        name="LOD Level",
        min = 0,
        max = 10,
        default = 0
        )
    lod_distance: FloatProperty(
        name="LOD Distance",
        min = 0.0,
        default = 0.0
        )

class LSArmatureProperties(PropertyGroup):
    skeleton_resource_id: StringProperty(
        name="Skeleton Resource UUID",
        default = ""
        )

class LSSceneProperties(PropertyGroup):
    game: EnumProperty(
        name="Game",
        description="The target game. Currently determines the model format type",
        items=game_versions,
        default=("bg3")
    )

class OBJECT_PT_LSPropertyPanel(Panel):
    bl_label = "BG3 Settings"
    bl_idname = "OBJECT_PT_ls_property_panel"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object"
    
    def draw(self, context):
        layout = self.layout
        if context.active_object.type == "MESH":
            props = context.active_object.data.ls_properties

            box = layout.box()
            box.label(text="Mesh Type")

            row = box.grid_flow()
            row.prop(props, "rigid")
            row.prop(props, "cloth")
            row.prop(props, "mesh_proxy")
            row.prop(props, "proxy")
            row.prop(props, "spring")
            row.prop(props, "occluder")
            row.prop(props, "impostor")

            layout.prop(props, "lod")
            layout.prop(props, "lod_distance")
        elif context.active_object.type == "ARMATURE":
            props = context.active_object.data.ls_properties
            layout.prop(props, "skeleton_resource_id")



class SCENE_PT_LSPropertyPanel(Panel):
    bl_label = "DOS2/BG3 Settings"
    bl_idname = "SCENE_PT_ls_property_panel"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "scene"
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.ls_properties
        layout.prop(props, "game")



class ColladaMetadataLoader:
    root = None
    SCHEMA = "{http://www.collada.org/2005/11/COLLADASchema}"

    TAG_TO_GAME = {
        "DivinityOriginalSin": "dos",
        "DivinityOriginalSinEE": "dosee",
        "DivinityOriginalSin2": "dos2",
        "DivinityOriginalSin2DE": "dos2de",
        "BaldursGate3PrePatch8": "bg3",
        "BaldursGate3": "bg3",
        "Unset": "unset",
    }

    def load_root_profile(self, context):
        profile = self.root.find(f"./{self.SCHEMA}extra/{self.SCHEMA}technique[@profile='LSTools']")
        if profile is None:
            report("LSLib profile data not found in Collada export; make sure you're using LSLib v1.16 or later!", "ERROR")
            return
        
        props = context.scene.ls_properties
        for ele in list(profile):
            if ele.tag == 'Game':
                props.game = self.TAG_TO_GAME[ele.text]


    def find_anim_settings(self):
        for anim in self.root.findall(f"./{self.SCHEMA}library_animations/{self.SCHEMA}animation"):
            settings = anim.find(f"{self.SCHEMA}extra/{self.SCHEMA}technique[@profile='LSTools']")
            if settings is not None:
                return settings

        return None
    
    def load_mesh_profile(self, geom, settings):
        mesh = bpy.data.meshes[geom.attrib['name']]
        props = mesh.ls_properties
        for ele in list(settings):
            if ele.tag == 'DivModelType':
                if ele.text == 'Rigid':
                    props.rigid = True
                elif ele.text == 'Cloth':
                    props.cloth = True
                elif ele.text == 'MeshProxy':
                    props.mesh_proxy = True
                elif ele.text == 'ProxyGeometry':
                    props.proxy = True
                elif ele.text == 'Spring':
                    props.spring = True
                elif ele.text == 'Occluder':
                    props.occluder = True
            elif ele.tag == 'IsImpostor' and ele.text == '1':
                props.impostor = True
            elif ele.tag == 'LOD':
                props.lod = int(ele.text)
            elif ele.tag == 'LODDistance':
                props.lod_distance = float(ele.text)
    
    def load_mesh_profiles(self):
        for geom in self.root.findall(f"./{self.SCHEMA}library_geometries/{self.SCHEMA}geometry"):
            settings = geom.find(f"{self.SCHEMA}mesh/{self.SCHEMA}extra/{self.SCHEMA}technique[@profile='LSTools']")
            if settings is not None:
                self.load_mesh_profile(geom, settings)

    def load_anim_profile(self, context, anim_settings):
        skel = anim_settings.find('SkeletonResourceID')
        skeleton_id = ""
        if skel is not None:
            skeleton_id = skel.text

        for obj in context.scene.objects:
            if obj.type == "ARMATURE" and obj.select_get():
                props = obj.data.ls_properties
                props.skeleton_resource_id = skeleton_id
    
    def load(self, context, collada_path):
        self.root = et.parse(collada_path).getroot()
        self.load_root_profile(context)
        anim_settings = self.find_anim_settings()
        self.load_mesh_profiles()
        if anim_settings is not None:
            self.load_anim_profile(context, anim_settings)



class DIVINITYEXPORTER_OT_import_collada(Operator, ImportHelper):
    bl_idname = "import_scene.dos2de_collada"
    bl_label = "Import Collada/GR2"
    bl_options = {"PRESET", "REGISTER", "UNDO"}

    filename_ext: StringProperty(
        name="File Extension",
        options={"HIDDEN"},
        default=".dae"
    )

    filter_glob: StringProperty(default="*.dae;*.gr2", options={"HIDDEN"})

    def fixup_bones(self, context):
        for obj in context.scene.objects:
            if obj.type == "ARMATURE" and obj.select_get():
                context.view_layer.objects.active = obj
                bpy.ops.object.mode_set(mode='EDIT')
                for bone in obj.data.edit_bones:
                    if len(bone.children) == 1:
                        bone.tail = bone.children[0].head
                    elif len(bone.children) == 0 and bone.parent is not None and len(bone.parent.children) == 1:
                        bone.use_connect = True
                bpy.ops.object.mode_set(mode='OBJECT')

        
    def execute(self, context):
        global current_operator
        try:
            current_operator = self
            return self.really_execute(context)
        finally:
            current_operator = None

    def really_execute(self, context):
        input_path = Path(self.properties.filepath)
        tempfile_path = None

        if input_path.suffix.lower() == '.gr2':
            addon_prefs = get_prefs(context)
            divine = DivineInvoker(addon_prefs, None)
            temp = tempfile.NamedTemporaryFile(delete=False)
            temp.close()
            tempfile_path = Path(temp.name)
            collada_path = tempfile_path
            if not divine.gr2_to_dae(str(input_path), str(collada_path)):
                return{'CANCELLED'}
        else:
            collada_path = input_path

        bpy.ops.wm.collada_import(filepath=str(collada_path), custom_normals=True, fix_orientation=True)

        meta_loader = ColladaMetadataLoader()
        meta_loader.load(context, str(collada_path))
        self.fixup_bones(context)

        if tempfile_path is not None:
            tempfile_path.unlink()

        report("Import completed successfully.", "INFO")
        return{'FINISHED'}

def export_menu_func(self, context):
    self.layout.operator(DIVINITYEXPORTER_OT_export_collada.bl_idname, text="DOS2/BG3 Collada (.dae, .gr2)")

def import_menu_func(self, context):
    self.layout.operator(DIVINITYEXPORTER_OT_import_collada.bl_idname, text="DOS2/BG3 Collada (.dae, .gr2)")

classes = (
    ProjectData,
    ProjectEntry,
    GR2_ExportSettings,
    Divine_ExportSettings,
    DIVINITYEXPORTER_OT_export_collada,
    DIVINITYEXPORTER_OT_import_collada,
    DIVINITYEXPORTER_OT_add_project,
    DIVINITYEXPORTER_OT_remove_project,
    DIVINITYEXPORTER_UL_project_list,
    DIVINITYEXPORTER_AddonPreferences,
    LSMeshProperties,
    LSArmatureProperties,
    LSSceneProperties,
    OBJECT_PT_LSPropertyPanel,
    SCENE_PT_LSPropertyPanel
)

def register():
    bpy.types.TOPBAR_MT_file_export.append(export_menu_func)
    bpy.types.TOPBAR_MT_file_import.append(import_menu_func)

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Mesh.ls_properties = PointerProperty(type=LSMeshProperties)
    bpy.types.Armature.ls_properties = PointerProperty(type=LSArmatureProperties)
    bpy.types.Scene.ls_properties = PointerProperty(type=LSSceneProperties)

    wm = bpy.context.window_manager
    km = wm.keyconfigs.addon.keymaps.new('Window', space_type='EMPTY', region_type='WINDOW', modal=False)

    km_export = km.keymap_items.new(DIVINITYEXPORTER_OT_export_collada.bl_idname, 'E', 'PRESS', ctrl=True, shift=True)
    km_import = km.keymap_items.new(DIVINITYEXPORTER_OT_import_collada.bl_idname, 'I', 'PRESS', ctrl=True, shift=True)
    addon_keymaps.append((km, km_export))
    addon_keymaps.append((km, km_import))


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(export_menu_func)
    bpy.types.TOPBAR_MT_file_import.remove(import_menu_func)

    for cls in classes:
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.ls_properties
    del bpy.types.Armature.ls_properties
    del bpy.types.Mesh.ls_properties

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        for km, kmi in addon_keymaps:
            km.keymap_items.remove(kmi)
    addon_keymaps.clear()
