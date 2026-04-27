from pydantic import BaseModel
import networkx as nx
import numpy as np
import osmnx as ox
import itertools
from networkx.algorithms.approximation import traveling_salesman_problem, christofides


START_COLOR = 4
# MODEL
class CourierConfig(BaseModel):
    daily_limit : float # daily limit to ride 
    start_point : int # index of vertex of start 
    

class TaskConfig(BaseModel):
    couriers : list[CourierConfig] # couriers with configs 
    delivery_points : set[int] # points of delivery 

class CourierSolution(BaseModel):
    route : list[int] = []
    length : float = 0.0

    def expand(self, G) -> "CourierSolution":
        if len(self.route) <= 1:
            return self
        res = []
        for i in range(len(self.route) - 1):
            res = res[:-1]
            res.extend(G[self.route[i]][self.route[i+1]]['path'])
        return CourierSolution(route=res, length=self.length)
    
    def verts_set(self) -> set[int]:
        return set(self.route)
    
    def edges_set(self) -> set[tuple[int, int]]:
        return set(
            (self.route[i], self.route[i + 1]) for i in range(len(self.route) - 1)
        )
    
    def tlength(self):
        return self.length
    
    def measure_insertion(self, G, v : int) -> float:
        NV = len(self.route)
        if NV == 0:
            raise ValueError("ERROR : NO VERTICES")
        if NV == 1:
            b = self.route[0]
            return G[b][v]['weight'] + G[v][b]['weight']
        vls = []
        for i in range(NV - 1):
            diff = G[self.route[i]][v]['weight'] + G[v][self.route[i + 1]]['weight'] - G[self.route[i]][self.route[i+1]]['weight']
            vls.append(diff)
        return min(vls)
    

class DayColoring(BaseModel):
    verts_dict : dict[int, int]
    edges_dict : dict[tuple[int, int], int]

class DaySolution(BaseModel):
    couriers_solutions : list[CourierSolution]
    orders_left : set[int]

    def expand(self, G) -> "DaySolution":
        return DaySolution(couriers_solutions=[
            cs.expand(G) for cs in self.couriers_solutions
        ], orders_left=self.orders_left)
    
    def verts_dict(self) -> dict[int, int]:
        dct = {}
        for i, cs in enumerate(self.couriers_solutions):
            for v in cs.verts_set():
                dct[v] = START_COLOR + i
        return dct

    def edges_dict(self) -> dict[tuple[int, int], int]:
        dct = {}
        for i, cs in enumerate(self.couriers_solutions):
            for v in cs.edges_set():
                dct[v] = START_COLOR + i
        return dct
    
    def day_coloring(self) -> DayColoring:
        return DayColoring(
            verts_dict=self.verts_dict(),
            edges_dict=self.edges_dict()
        )
    
    def tlength(self):
        return sum(sol.tlength() for sol in self.couriers_solutions)


class Coloring(BaseModel):
    colorings : list[DayColoring]

class Solution(BaseModel):
    day_solutions : list[DaySolution]

    def coloring(self):
        return Coloring(colorings=[sol.day_coloring() for sol in self.day_solutions])
    
    def tlength(self):
        return sum(sol.tlength() for sol in self.day_solutions)


# visual + analyzing

def relabel(G): #takes osmnx graph and rationally relabelling it 
    mapping = {node: i for i, node in enumerate(G.nodes())}
    G = nx.relabel_nodes(G, mapping, copy=False)


def calculate_asymmetry_metric(G, weight='weight'):
    dist_dict = dict(nx.all_pairs_dijkstra_path_length(G, weight=weight))
    
    nodes = list(G.nodes())
    n = len(nodes)
    dist_matrix = np.full((n, n), np.inf)
    node_to_idx = {node: i for i, node in enumerate(nodes)}
    
    for u, targets in dist_dict.items():
        for v, d in targets.items():
            dist_matrix[node_to_idx[u], node_to_idx[v]] = d

    d_ij = dist_matrix
    d_ji = dist_matrix.T
    
    mask = (d_ij < np.inf) & (d_ji < np.inf) & (~np.eye(n, dtype=bool))
    
    if not np.any(mask):
        return 0.0

    vals_ij = d_ij[mask]
    vals_ji = d_ji[mask]
    
    relative_diffs = (vals_ij - vals_ji) / (vals_ij + vals_ji)
    
    m_squared = np.mean(np.square(relative_diffs))
    m = np.sqrt(m_squared)
    
    return m

# some methods
#def dist_shrink()

def path_shrink(G):
    return ox.simplify_graph(G)

class Unsolvable(Exception):
    pass 


class UndirSolution:
    def __init__(self, G, task : TaskConfig):
        self.task = task 
        self.depos = set(conf.start_point for conf in task.couriers)
        dps = task.delivery_points
        self.G = nx.DiGraph()
        self.SG = nx.Graph()
        self.NC = len(task.couriers)
        self.exceptions = [set() for _ in range(self.NC)]
        
        
        for u, v in itertools.product(dps | self.depos, repeat=2):
            try:
                dist, path = nx.single_source_dijkstra(G, u, target=v, weight='length')
                print(u, v, path, dist)
                
                self.G.add_edge(u, v, weight=dist, path=path)

                cdist = dist
                if self.SG.has_edge(u, v):
                    cdist = (self.SG[u][v]['weight'] + cdist) / 2
                self.SG.add_edge(u, v, weight = cdist)

            except nx.NetworkXNoPath as e:
                continue
                #raise e
        
        #self.orders = set(self.task.delivery_points)

        
                
    #def closer_depo_out(self, vert : int) -> float:
    #    return min(self.G[vert][depo]['weight'] + self.G[depo][vert]['weight'] for depo in self.depos)
    
    def measure_path(self, verts : list[int]) -> float:
        sm = 0.0
        for i in range(len(verts) - 1):
            sm += self.G[verts[i]][verts[i + 1]]['weight']
        return sm
    
    def christ_measure(self, verts : list[int]) -> tuple[float, list[int]]:
        pth =  traveling_salesman_problem(
                self.SG, 
                nodes=verts, 
                weight='weight', 
                method=christofides
            )
        return self.measure_path(pth), pth
    
    def daily_solution(self, orders : set[int]) -> tuple[DaySolution, bool]:
        working_couriers = set(range(self.NC))
        cour_sols = [CourierSolution(route=[ccf.start_point]) for ccf in self.task.couriers]
        
        def extract_pair() -> tuple[int, int]: #number of courier, number of order
            def measure(pt : tuple[int, int]) -> float:
                cour, order = pt 
                try:
                    return cour_sols[cour].measure_insertion(self.G, order)
                except:
                    return float("inf")
            pairs = itertools.chain.from_iterable(
                itertools.product([cour], orders - self.exceptions[cour])
                    for cour in working_couriers
            )
            return min(pairs, key=measure)
        
        is_stop = True
        
        while working_couriers and orders:
            try:
                cour, order = extract_pair()
            except:
                is_stop = True
                break
            new_lst = cour_sols[cour].route + [order]
            try:
                msr, npath = self.christ_measure(new_lst)
            except:
                self.exceptions[cour].add(order)
                continue
            
            if msr > self.task.couriers[cour].daily_limit:
                working_couriers.remove(cour)
                continue
            else: 
                cour_sols[cour] = CourierSolution(route = npath, length=msr)
                orders.remove(order)
                is_stop = False
        
        
        return DaySolution(couriers_solutions=cour_sols, orders_left=orders.copy()).expand(self.G), is_stop


    def solve(self) -> tuple[Solution, bool]:
        sols = []
        all_orders = self.task.delivery_points.copy()
        while all_orders:
            daily, ended = self.daily_solution(all_orders)
            if ended:
                break
            sols.append(daily)
        return Solution(day_solutions=sols), not all_orders


            

