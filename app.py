import osmnx as ox
import pandas as pd
import streamlit as st
import pydeck as pdk
import numpy as np
from model_tools import * #relabel, calculate_asymmetry_metric
import colorsys
import random
import streamlit.components.v1 as components
import json

st.set_page_config(layout="wide")
st.title("VRP")




# --- JS hook ---

#print(st.session_state.get("component_value"))



# --- 1. bbox ---
bbox = (27.5570, 53.9005, 27.5610, 53.9030)

def shift_bbox(bbx, pad):
    w, s, e, n = bbx
    return (w - pad, s - pad, e + pad, n + pad)

bbox = shift_bbox(bbox, 0.003)

# --- 2. загрузка графа ---
@st.cache_data
def load_graph():
    G = ox.graph_from_bbox(
        bbox=bbox,
        network_type="all",
        simplify=False
    )
    relabel(G)
    #G = path_shrink(G)
    #print(calculate_asymmetry_metric(G))
    nodes, edges = ox.graph_to_gdfs(G)
    return G, nodes, edges

G, nodes, edges = load_graph()

# --- 3. геометрия "палочки" (┤) ---
def create_arrow(p1, p2, k=0.20, min_size=0.00001, max_size=0.00007, angle_deg=19):
    lon1, lat1 = p1
    lon2, lat2 = p2

    # --- коррекция долгот ---
    scale = np.cos(np.radians((lat1 + lat2) / 2))

    p1c = np.array([lon1 * scale, lat1])
    p2c = np.array([lon2 * scale, lat2])

    vec = p2c - p1c
    length = np.linalg.norm(vec)
    if length == 0:
        return []

    d = vec / length

    # 🔥 --- масштаб стрелки от длины ребра ---
    size = np.clip(length * k, min_size, max_size)

    angle = np.radians(angle_deg)

    def rotate(v, ang):
        return np.array([
            v[0] * np.cos(ang) - v[1] * np.sin(ang),
            v[0] * np.sin(ang) + v[1] * np.cos(ang)
        ])

    left_dir = rotate(-d, angle)
    right_dir = rotate(-d, -angle)

    # 🔥 чуть отодвигаем назад (важно!)
    base = p2c - d * size * 0.3

    left = base + left_dir * size
    right = base + right_dir * size

    # обратно
    left = [left[0] / scale, left[1]]
    right = [right[0] / scale, right[1]]
    base = [base[0] / scale, base[1]]

    return [
        [base, left],
        [base, right]
    ]

# --- 4. универсальный рендер ---
#@st.cache_data
@st.cache_data
def graph_data():
    line_rows = []
    node_rows = []

    for (u, v, k), row in edges.iterrows():
        if u not in nodes.index or v not in nodes.index:
            continue

        start = [nodes.loc[u].x, nodes.loc[u].y]
        end = [nodes.loc[v].x, nodes.loc[v].y]

        line_rows.append({
            "coordinates": [start, end],
            "edge_id" : (u, v, k),
            "tooltip_text" : f"Edge {k} from vert {u} to vert {v}"

        })

        # --- добавляем "палочку" если oneway ---
        if row.get("oneway", False):
            arrows = create_arrow(start, end)
            for line in arrows:
                line_rows.append({
                    "coordinates": line,
                    "edge_id" : (u, v, k)
                })

    # --- nodes ---
    for node_id, row in nodes.iterrows():
        node_rows.append({
            "position": [row.x, row.y],
            "tooltip_text" : f"node {node_id}",
            "node_id" : node_id
        })

    return pd.DataFrame(node_rows), pd.DataFrame(line_rows)


def courier_color_idx(vert):
    c_verts = [c.start_point for c in st.session_state['task_config'].couriers]
    try:
        return c_verts.index(vert)
    except ValueError:
        return None

if "view_state" not in st.session_state:
    center = nodes.geometry.union_all().centroid
    st.session_state["view_state"] = pdk.ViewState(
            latitude=center.y,
            longitude=center.x,
            zoom=16,
            #controller=True
        )


def render_graph(df_points, df_lines,node_style_fn, edge_style_fn):
    layers = []

    df_points['color'] = df_points.apply(node_style_fn, axis = 1)
    df_points['radius'] = df_points.apply(lambda row : 4 if courier_color_idx(row['node_id']) is None else 29, axis = 1)

    df_lines['color'] = df_lines.apply(edge_style_fn, axis = 1)
    df_lines['width'] = 2

    # --- рёбра ---
    layers.append(
        pdk.Layer(
            "PathLayer",
            data=df_lines,
            #id="verts",
            get_path="coordinates",
            get_color="color",
            get_width="width",
            width_units="pixels",
            width_min_pixels=1,
            width_max_pixels=4,
            #pickable = True,
        )
    )


    # --- вершины ---
    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=df_points, #.to_dict(orient="records"),
            id="verts",
            get_position="position",
            get_fill_color="color",
            get_radius="radius",
            radius_min_pixels=1,
            radius_max_pixels=6,
            pickable = True,
            #use_binary_transport=False
        )
    )


    return pdk.Deck(
        layers=layers,
        initial_view_state=st.session_state['view_state'],
        tooltip={
            "html": "<b>Информация:</b><br/>{tooltip_text}",
            "style": {"backgroundColor": "steelblue", "color": "white"}
        }
    )

# --- 5. кастомная логика окраски ---
def get_distinct_color(index):
    if not index:
        return [158,158,158]
    PHI = 0.618033988749895
    hue = (index * PHI) % 1
    rgb = colorsys.hsv_to_rgb(hue, 0.8, 0.95)
    return [int(x * 255) for x in rgb]

color = list[int]




if 'task_config' not in st.session_state:
    st.session_state['task_config'] = TaskConfig(couriers=[], delivery_points=set())

if 'start_color' not in st.session_state:
    st.session_state['start_color'] = START_COLOR

if 'selected_verts' not in st.session_state:
    st.session_state['selected_verts'] = set()

if 'used_verts' not in st.session_state:
    st.session_state['used_verts'] = set()

if 'map_counter' not in st.session_state:
    st.session_state['map_counter'] = 0

# solutioni
if 'solution' not in st.session_state:
    st.session_state['solution'] = None

if 'full_solution' not in st.session_state:
    st.session_state['full_solution'] = None

if 'full_coloring' not in st.session_state:
    st.session_state['full_coloring'] = None

if 'current_day' not in st.session_state:
    st.session_state['current_day'] = None


#st.session_state = {
#    'coloring_state' : ColoringState(vert_coloring={}, edge_coloring={}),
#    'start_color' : 3,
#    'task_config' : TaskConfig(couriers=[], delivery_points=[])
#}

def edge_style(row):
    clr = get_distinct_color(0)
    if st.session_state['solution'] is not None:
        day = st.session_state['current_day']
        day_map = st.session_state['full_coloring'].colorings[day].edges_dict
        for ed in [row['edge_id'][:2], row['edge_id'][:2][::-1]]:
            poss_clr = day_map.get(ed)
            if poss_clr is not None:
                clr = get_distinct_color(poss_clr)
                break
    return clr


def node_style(row):
    clr = get_distinct_color(0)
    if row['node_id'] in st.session_state['selected_verts']:
        clr = get_distinct_color(2)
    if row['node_id'] in st.session_state['task_config'].delivery_points:
        clr = get_distinct_color(3)
        if st.session_state['solution'] is not None:
            day = st.session_state['current_day']
            day_sol = st.session_state['solution'].day_solutions[day].orders_left
            if row['node_id'] not in day_sol:
                clr = get_distinct_color(0)
    cclri = courier_color_idx(row['node_id'])
    if cclri is not None:
        clr = get_distinct_color(st.session_state['start_color'] + cclri)
    
    if st.session_state['solution'] is not None:
        day = st.session_state['current_day']
        day_map = st.session_state['full_coloring'].colorings[day].verts_dict
        poss_clr = day_map.get(row['node_id'])
        if poss_clr is not None:
            clr = get_distinct_color(poss_clr)
    
    return clr

# --- 6. UI ---
#st.sidebar.header("Фильтры")
#show_nodes = st.sidebar.checkbox("Показать вершины", True)

# --- PROCESS DATA --- 
df_points, df_lines = graph_data()

# --- event reaction ---

# --- RENDER ---
deck = render_graph(df_points, df_lines, node_style, edge_style)
map_key = f"map_{st.session_state['map_counter']}"

cht = st.pydeck_chart(deck, on_select='rerun', selection_mode='single-object', key = map_key)
#print(cht['view_state'])

selected = st.session_state[map_key].get("selection",{}).get("objects", {}).get("verts", {})
#print(st.session_state['map'])
selected_id = selected[0]['node_id'] if selected else None 




def rerun():
    #print(current_view_state_raw())
    st.session_state['map_counter'] += 1
    st.rerun()

def selected_list(container=st):
    with container.expander("Выбранные", expanded=False):
        if not st.session_state["selected_verts"]:
            st.write("Ничего не выбрано")
        else:
            # Сортируем для стабильности интерфейса
            old = st.session_state['selected_verts'].copy()

            for vert_id in sorted(list(old)):
                c1, c2 = st.columns([0.8, 0.2])
                c1.text(f"ID: {vert_id}")
                if c2.button("❌", key=f"del_{vert_id}"):
                    old.remove(vert_id)
                    st.session_state['selected_verts'] = old 
                    rerun()

def input_slider(container, label, min_value, max_value, step, key):
    slider_key = f"{key}_slider_internal"
    input_key = f"{key}_input_internal"

    if slider_key not in st.session_state:
        st.session_state[slider_key] = min_value
    if input_key not in st.session_state:
        st.session_state[input_key] = min_value

    def sync_to_input():
        st.session_state[input_key] = st.session_state[slider_key]

    def sync_to_slider():
        val = st.session_state[input_key]
        st.session_state[slider_key] = max(min_value, min(max_value, val))

    st.write(label)
    col_slider, col_input = container.columns([3, 1])

    with col_slider:
        st.slider(
            "label_hidden", min_value, max_value, 
            key=slider_key, 
            on_change=sync_to_input,
            step=step,
            label_visibility="collapsed"
        )
    
    with col_input:
        st.number_input(
            "label_hidden", min_value, max_value, 
            key=input_key, 
            on_change=sync_to_slider,
            label_visibility="collapsed"
        )

    return st.session_state[slider_key]

    
def random_selection(container=st):
    with container.expander("Случайный выбор", expanded=True):
        used = st.session_state['used_verts']
        sel = st.session_state['selected_verts']
        tot = set(range(len(df_points)))
        pt_count = input_slider(st, "Количество вершин", min_value=0, max_value=len(tot) - len(sel) - len(used), step=1, key="RS")

        if st.button("Набрать"):
            for_choice = tot - used - sel 
            choosen = set(random.sample(list(for_choice), pt_count))
            new_sel = st.session_state['selected_verts'].copy().union(choosen)
            st.session_state['selected_verts'] = new_sel 
            rerun()
            #pass

def selection_menu(container=st):
    with container.expander(f"Выбор вершин ({len(st.session_state['selected_verts'])})", expanded=True):
        selected_list()
        random_selection()

def as_orders():
    if st.button("Заказы"):
        if not st.session_state['selected_verts']:
            st.toast("Нет выбранных точек!")
            return
        st.session_state['task_config'].delivery_points.update(st.session_state['selected_verts'])
        st.session_state['used_verts'].update(st.session_state['selected_verts'])
        st.session_state['selected_verts'].clear()
        rerun()

def as_couriers():
    if st.button("Курьеры"):
        if not st.session_state['selected_verts']:
            st.toast("Нет выбранных точек!")
            return
        courier_confs = [CourierConfig(daily_limit=0, start_point=pt) for pt in st.session_state['selected_verts']]
        st.session_state['task_config'].couriers.extend(courier_confs)
        st.session_state['used_verts'].update(st.session_state['selected_verts'])
        st.session_state['selected_verts'].clear()
        rerun()

def courier_menu(): # return task config
    with st.expander("Курьеры", expanded=True):
        res_c_confs = []
        for i, c_conf in enumerate(st.session_state['task_config'].couriers):
            c1, c2 = st.columns([0.2, 0.8])
            clr = get_distinct_color(st.session_state['start_color'] + i)
            c1.markdown(
                f'<div style="width: 20px; height: 20px; background-color: rgb({clr[0]}, {clr[1]}, {clr[2]}); '
                f'border-radius: 50%; margin-top: 10px;"></div>', 
                unsafe_allow_html=True
            )
            vl = input_slider(c2, label="Дневной пробег",min_value=0.0, max_value=10000.0, step=0.1, key=i)
            res_c_confs.append(CourierConfig(daily_limit=vl, start_point=c_conf.start_point))
        return TaskConfig(couriers=res_c_confs, delivery_points=st.session_state['task_config'].delivery_points)     
        

def conversion_menu():
    with st.expander("Отметить как", expanded=False):
        as_orders()
        as_couriers()

with st.sidebar:
    if st.session_state['solution'] is None:
        selection_menu()
        conversion_menu()
        task_config = courier_menu()
        if st.button("SOLVE"):
            solver = UndirSolution(G, task_config)
            solshn, full = solver.solve()
            print(task_config)
            print(solshn)
            st.session_state['solution'] = solshn 
            st.session_state['full_solution'] = full 
            st.session_state['full_coloring'] = solshn.coloring()
            st.session_state['current_day'] = 0
            st.rerun()
    else:
        comment = 'Полное' if st.session_state['full_solution'] else "Неполное"
        ND = len(st.session_state['solution'].day_solutions)
        NC = len(st.session_state['task_config'].couriers)
        with st.expander(f"Решение({comment}):", expanded=True):
            if st.session_state['solution'].day_solutions:

                day = st.radio(
                    "День",
                    list(range(ND)),
                    index=st.session_state['current_day'],
                    format_func=lambda x: f"День {x+1}"
                )

                if day != st.session_state['current_day']:
                    st.session_state['current_day'] = day
                    st.rerun()
        sol : Solution = st.session_state['solution']
        with st.expander(f"Статистика(пройдено : {sol.tlength()})", expanded=True):
            for d in range(ND):
                with st.expander(f"День {d + 1}(пройдено : {sol.day_solutions[d].tlength()})",expanded=False):
                    for i in range(NC):
                        c1, c2 = st.columns([0.2, 0.8])
                        clr = get_distinct_color(st.session_state['start_color'] + i)
                        c1.markdown(
                            f'<div style="width: 20px; height: 20px; background-color: rgb({clr[0]}, {clr[1]}, {clr[2]}); '
                            f'border-radius: 50%; margin-top: 10px;"></div>', 
                            unsafe_allow_html=True
                        )
                        c2.text(f'пройдено : {sol.day_solutions[d].couriers_solutions[i].tlength()}')
    
            



if selected_id:
    if selected_id in st.session_state["used_verts"]:
        pass
    else:
        old = st.session_state['selected_verts'].copy()
        if selected_id in old:
            old.remove(selected_id)
        else:
            old.add(selected_id)
        st.session_state['selected_verts'] = old 
    rerun()



