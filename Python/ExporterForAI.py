import json
import os
import datetime
import unreal

# ========== CONFIG ==========
# Si está vacío, exporta los assets SELECCIONADOS.
# Usa rutas del Content Browser que comiencen en /Game (NO /Content).
SEARCH_PATHS = ["/Game/RicardoJF/EnemySystem"]  # e.g., ["/Game/Blueprints"]

# Carpeta de salida = subcarpeta junto a este .py
DEFAULT_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Exports_JSON")

# Exportar también variables y componentes del SCS (best-effort, puede variar por versión)
EXPORT_VARIABLES = True
EXPORT_SCS_COMPONENTS = True
# ============================


# ---------- Utilidades ----------
def ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
    return path


def safe_name(obj):
    try:
        return obj.get_name()
    except Exception:
        try:
            return str(obj)
        except Exception:
            return "Unknown"


def normalize_content_path(p: str) -> str:
    """Convierte \ a / y /Content → /Game."""
    p = p.replace("\\", "/")
    if p.startswith("/Content/"):
        return p.replace("/Content/", "/Game/", 1)
    return p


# ---------- Serialización de nodos/pines ----------
def get_node_title(node):
    try:
        return node.get_node_title(unreal.NodeTitleType.FULL_TITLE)
    except Exception:
        try:
            return node.get_node_title(unreal.NodeTitleType.LIST_VIEW)
        except Exception:
            try:
                return node.get_editor_property("node_comment") or safe_name(node)
            except Exception:
                return safe_name(node)


def serialize_pin_type(pin_type):
    data = {}
    try:
        data["category"] = pin_type.pin_category
        data["subcategory"] = pin_type.pin_subcategory
        data["is_map"] = pin_type.is_map
        data["is_array"] = pin_type.is_array
        data["is_set"] = pin_type.is_set
        try:
            sub_obj = pin_type.pin_subcategory_object
            data["subcategory_object"] = str(sub_obj) if sub_obj else None
        except Exception:
            data["subcategory_object"] = None
    except Exception:
        pass
    return data


def serialize_pin(pin, node_index_by_obj):
    d = {}
    try:
        d["name"] = pin.pin_name
    except Exception:
        d["name"] = None

    try:
        d["direction"] = str(pin.direction)  # EEdGraphPinDirection
    except Exception:
        d["direction"] = None

    try:
        d["type"] = serialize_pin_type(pin.pin_type)
    except Exception:
        d["type"] = None

    # Default values
    for attr in ("default_value", "default_text_value", "default_object"):
        if hasattr(pin, attr):
            try:
                v = getattr(pin, attr)
                if attr == "default_object" and v:
                    v = safe_name(v)
                if v not in (None, "", 0):
                    d[attr] = v
            except Exception:
                pass

    # Enlaces
    links = []
    try:
        for linked in pin.linked_to:
            try:
                owner_node = linked.get_outer()  # EdGraphNode
            except Exception:
                owner_node = None

            links.append({
                "target_node_id": node_index_by_obj.get(owner_node, None),
                "target_pin_name": getattr(linked, "pin_name", None)
            })
    except Exception:
        pass
    d["links"] = links
    return d


def is_k2_node(node):
    try:
        return isinstance(node, unreal.K2Node)
    except Exception:
        return True


def serialize_node(node, node_id, node_index_by_obj):
    d = {
        "id": node_id,
        "class": None,
        "title": None,
        "pos": {"x": None, "y": None},
        "comment": None,
        "pins": [],
        "flags": {}
    }

    try:
        d["class"] = node.get_class().get_name()
    except Exception:
        d["class"] = node.__class__.__name__

    d["title"] = str(get_node_title(node))

    try:
        d["pos"]["x"] = int(node.node_pos_x)
        d["pos"]["y"] = int(node.node_pos_y)
    except Exception:
        pass

    try:
        d["comment"] = node.node_comment
    except Exception:
        pass

    if is_k2_node(node):
        for flag_attr in ("is_pure", "is_const", "is_inline_node", "is_compact_node"):
            if hasattr(node, flag_attr):
                try:
                    d["flags"][flag_attr] = bool(getattr(node, flag_attr)())
                except Exception:
                    pass

    try:
        d["pins"] = [serialize_pin(p, node_index_by_obj) for p in node.pins]
    except Exception:
        d["pins"] = []

    return d


# ---------- Graphs ----------
def graph_label(bp, graph):
    name = safe_name(graph)
    label = {"name": name, "kind": "Unknown"}

    try:
        outer = graph.get_outer()
        outer_cls = outer.get_class().get_name() if outer else ""
        if isinstance(outer, unreal.Blueprint):
            if "EventGraph" in name or "Event Graph" in name:
                label["kind"] = "EventGraph"
            else:
                label["kind"] = "Graph"
        elif isinstance(outer, unreal.EdGraph):
            label["kind"] = "SubGraph"
        else:
            if "Function" in outer_cls:
                label["kind"] = "FunctionGraph"
            elif "Macro" in outer_cls:
                label["kind"] = "MacroGraph"
    except Exception:
        pass

    low = name.lower()
    if "event graph" in low or low == "eventgraph":
        label["kind"] = "EventGraph"
    if "macro" in low and label["kind"] == "Unknown":
        label["kind"] = "MacroGraph"
    if ("func" in low or "function" in low) and label["kind"] == "Unknown":
        label["kind"] = "FunctionGraph"

    return label


def edgraph_nodes_portable(graph):
    # 1) propiedad pública 'nodes'
    try:
        nodes = list(getattr(graph, "nodes"))
        if nodes:
            return nodes
    except Exception:
        pass
    # 2) método get_nodes (si existe)
    try:
        nodes = list(graph.get_nodes())
        return nodes
    except Exception:
        pass
    return []


def serialize_graph(bp, graph):
    meta = graph_label(bp, graph)
    nodes = edgraph_nodes_portable(graph)
    node_index_by_obj = {n: i for i, n in enumerate(nodes)}
    nodes_out = [serialize_node(n, i, node_index_by_obj) for i, n in enumerate(nodes)]
    return {
        "name": meta["name"],
        "kind": meta["kind"],
        "nodes_count": len(nodes_out),
        "nodes": nodes_out
    }


# ---------- Variables / SCS ----------
def serialize_variables_portable(bp):
    out = []
    if not EXPORT_VARIABLES:
        return out
    try:
        for desc in getattr(bp, "new_variables", []) or []:
            name = getattr(desc, "var_name", None)
            vtype = getattr(desc, "var_type", None)
            dv = getattr(desc, "default_value", None) if hasattr(desc, "default_value") else None
            type_info = None
            if vtype:
                type_info = {
                    "category": getattr(vtype, "pin_category", None),
                    "subcategory": getattr(vtype, "pin_subcategory", None),
                    "subcategory_object": str(getattr(vtype, "pin_subcategory_object", None)) if getattr(vtype, "pin_subcategory_object", None) else None,
                    "is_array": getattr(vtype, "is_array", None),
                    "is_set": getattr(vtype, "is_set", None),
                    "is_map": getattr(vtype, "is_map", None),
                }
            out.append({"name": str(name), "type": type_info, "default_value": dv})
    except Exception:
        pass
    return out


def serialize_scs(bp):
    res = []
    if not EXPORT_SCS_COMPONENTS:
        return res
    try:
        scs = getattr(bp, "simple_construction_script", None)
        if not scs:
            return res
        for n in scs.get_all_nodes():
            comp = getattr(n, "component_template", None)
            parent = None
            try:
                parent_node = n.get_parent()
                parent = safe_name(parent_node) if parent_node else None
            except Exception:
                pass
            loc = getattr(comp, "relative_location", None) if comp else None
            rot = getattr(comp, "relative_rotation", None) if comp else None
            scl = getattr(comp, "relative_scale3d", None) if comp else None
            res.append({
                "node_name": safe_name(n),
                "component_class": comp.get_class().get_name() if comp else None,
                "parent": parent,
                "transform": {
                    "location": [getattr(loc, "x", None), getattr(loc, "y", None), getattr(loc, "z", None)] if loc else None,
                    "rotation": [getattr(rot, "pitch", None), getattr(rot, "yaw", None), getattr(rot, "roll", None)] if rot else None,
                    "scale": [getattr(scl, "x", None), getattr(scl, "y", None), getattr(scl, "z", None)] if scl else None,
                }
            })
    except Exception:
        pass
    return res


# ---------- Carga/compilación y obtención de graphs ----------
def force_load_blueprint(bp):
    """Abre el BP en el editor y lo compila para hidratar sus propiedades editoriales."""
    try:
        aes = unreal.get_editor_subsystem(unreal.AssetEditorSubsystem)
        aes.open_editor_for_assets([bp])  # API moderna
    except Exception as e:
        unreal.log_warning(f"[BP→JSON] No se pudo abrir editor de {safe_name(bp)}: {e}")

    try:
        unreal.KismetEditorUtilities.compile_blueprint(bp)
    except Exception as e:
        unreal.log_warning(f"[BP→JSON] No se pudo compilar {safe_name(bp)}: {e}")


def get_all_graphs_portable(bp):
    """
    Devuelve lista de EdGraph del Blueprint usando múltiples propiedades conocidas.
    Evita BlueprintEditorLibrary.get_all_graphs (no existe en algunas versiones).
    """
    graphs = []
    prop_names = [
        "ubergraph_pages",
        "function_graphs",
        "macro_graphs",
        "delegate_signature_graphs",
        "intermediate_generated_graphs",
        "graphs",
    ]
    for pn in prop_names:
        try:
            if hasattr(bp, pn):
                val = getattr(bp, pn)
                if val:
                    graphs.extend(list(val))
        except Exception:
            pass

    # elimina duplicados preservando orden
    seen = set()
    uniq = []
    for g in graphs:
        try:
            gid = int(g.__hash__())
        except Exception:
            gid = id(g)
        if gid not in seen:
            uniq.append(g)
            seen.add(gid)
    return uniq


# ---------- Recolectores ----------
def gather_blueprints_from_selection():
    assets = unreal.EditorUtilityLibrary.get_selected_assets()
    return [a for a in assets if isinstance(a, unreal.Blueprint) or hasattr(a, "generated_class")]


def gather_blueprints_from_paths(paths):
    """Listar assets por ruta, cargar y filtrar por Blueprints de cualquier tipo."""
    out = []
    for raw in paths:
        p = normalize_content_path(raw)
        asset_paths = unreal.EditorAssetLibrary.list_assets(p, recursive=True, include_folder=False)
        for asset_path in asset_paths:
            obj = unreal.EditorAssetLibrary.load_asset(asset_path)
            if not obj:
                continue
            if isinstance(obj, unreal.Blueprint) or hasattr(obj, "generated_class"):
                out.append(obj)
    return out


# ---------- Serialización de un Blueprint ----------
def serialize_blueprint(bp):
    data = {
        "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
        "asset_path": unreal.EditorAssetLibrary.get_path_name_for_loaded_asset(bp),
        "asset_name": safe_name(bp),
        "blueprint_class": bp.get_class().get_name(),
        "generated_class": None,
        "parent_class": None,
        "interfaces": [],
        "is_data_only": None,
        "num_graphs": 0,
        "num_variables": 0,
        "num_components": 0,
        "variables": [],
        "components_scs": [],
        "graphs": [],
        "functions": [],
        "macros": [],
        "events": [],
    }

    # Clases (generada y padre)
    try:
        gen_class = getattr(bp, "generated_class", None)
        data["generated_class"] = safe_name(gen_class) if gen_class else None
        parent = getattr(bp, "parent_class", None)
        if not parent and gen_class:
            try:
                parent = gen_class.get_super_class()
            except Exception:
                parent = None
        data["parent_class"] = safe_name(parent) if parent else None
    except Exception:
        pass

    # Interfaces implementadas
    try:
        interfaces = []
        for impl in getattr(bp, "implemented_interfaces", []) or []:
            interfaces.append({
                "interface_name": safe_name(getattr(impl, "interface", None)),
                "graph_name": safe_name(getattr(impl, "graph", None)),
                "implemented_by_k2": bool(getattr(impl, "bImplementedByK2", False))
            })
        data["interfaces"] = interfaces
    except Exception:
        pass

    # Data-only flag
    try:
        data["is_data_only"] = bool(bp.is_data_only_blueprint())
    except Exception:
        data["is_data_only"] = None

    # Variables y SCS
    data["variables"] = serialize_variables_portable(bp)
    data["num_variables"] = len(data["variables"])

    data["components_scs"] = serialize_scs(bp)
    data["num_components"] = len(data["components_scs"])

    # Graphs
    force_load_blueprint(bp)
    graphs = get_all_graphs_portable(bp)
    data["num_graphs"] = len(graphs)

    for g in graphs:
        try:
            gi = serialize_graph(bp, g)
            k = gi["kind"]
            if k == "FunctionGraph":
                data["functions"].append(gi)
            elif k == "MacroGraph":
                data["macros"].append(gi)
            else:
                data["graphs"].append(gi)
        except Exception as ex:
            unreal.log_warning(f"[BP→JSON] Fallo al procesar graph {safe_name(g)}: {ex}")

    # Eventos detectados
    events = []
    for g in data["graphs"]:
        for n in g.get("nodes", []):
            cls = (n.get("class") or "")
            ttl = (n.get("title") or "")
            if "K2Node_Event" in cls or ttl.startswith("Event "):
                events.append({"graph": g["name"], "event": ttl, "pos": n.get("pos")})
    data["events"] = events

    return data


# ---------- Main ----------
def main():
    out_dir = ensure_dir(DEFAULT_OUT_DIR)

    if SEARCH_PATHS:
        norm = [normalize_content_path(p) for p in SEARCH_PATHS]
        unreal.log(f"[BP→JSON] Rutas buscadas (normalizadas): {norm}")
        bps = gather_blueprints_from_paths(norm)
    else:
        bps = gather_blueprints_from_selection()

    unreal.log(f"[BP→JSON] Encontrados: {len(bps)} Blueprints")
    if not bps:
        unreal.log_warning("[BP→JSON] No se encontraron Blueprints (verifica la ruta: debe iniciar con /Game).")
        return

    unreal.log("[BP→JSON] Exportando {} Blueprints a: {}".format(len(bps), out_dir))

    for bp in bps:
        try:
            data = serialize_blueprint(bp)
            # Nombre de archivo seguro
            asset_path = data["asset_path"].replace("/", "_").replace(".", "_")
            fname = f"{asset_path}.json"
            fpath = os.path.join(out_dir, fname)
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            unreal.log("[BP→JSON] OK: {}".format(fpath))
        except Exception as e:
            unreal.log_error(f"[BP→JSON] ERROR con {safe_name(bp)}: {e}")

    unreal.log("[BP→JSON] Listo.")


if __name__ == "__main__":
    main()
