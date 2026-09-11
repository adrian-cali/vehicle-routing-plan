"""H3 clustering service for VRP optimization (R&D feature)"""
from typing import List, Dict, Tuple, Optional
from core.config import get_settings
from services.h3_utils import latlng_to_h3, h3_cell_to_latlng


class H3Service:
    def __init__(self, resolution: int = None):
        settings = get_settings()
        self.resolution = resolution or getattr(settings, "h3_default_resolution", 7)
        # feature toggle may not be present in settings; default to False
        self.enabled = getattr(settings, "h3_enabled", False)
    
    def get_h3_cell(self, lat: float, lng: float) -> str:
        """Convert lat/lng to H3 cell"""
        return latlng_to_h3(float(lat), float(lng), int(self.resolution))
    
    def cluster_tasks_by_h3(self, tasks: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Cluster tasks by H3 cell
        """
        if not self.enabled:
            return {"default": tasks}
        
        clusters = {}
        for task in tasks:
            if task.get("latitude") and task.get("longitude"):
                cell = self.get_h3_cell(task["latitude"], task["longitude"])
                if cell not in clusters:
                    clusters[cell] = []
                clusters[cell].append(task)
        
        return clusters
    
    def get_cluster_center(self, cell: str) -> Tuple[float, float]:
        """Get center point of H3 cell"""
        lat, lng = h3_cell_to_latlng(cell)
        return lat, lng
    
    def assign_fms_to_clusters(
        self,
        clusters: Dict[str, List[Dict]],
        fm_locations: Dict[str, Tuple[float, float]]
    ) -> Dict[str, List[str]]:
        """
        Simple heuristic: assign FMs to nearest clusters
        """
        cluster_assignments = {cell: [] for cell in clusters.keys()}
        
        cluster_centers = {
            cell: self.get_cluster_center(cell)
            for cell in clusters.keys()
        }
        
        for fm_id, fm_loc in fm_locations.items():
            nearest_cell = min(
                cluster_centers.keys(),
                key=lambda c: self._haversine_distance(fm_loc, cluster_centers[c])
            )
            cluster_assignments[nearest_cell].append(fm_id)
        
        return cluster_assignments
    
    def _haversine_distance(self, loc1: Tuple[float, float], loc2: Tuple[float, float]) -> float:
        from math import radians, sin, cos, sqrt, atan2
        
        lat1, lng1 = map(radians, loc1)
        lat2, lng2 = map(radians, loc2)
        
        dlat = lat2 - lat1
        dlng = lng2 - lng1
        
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlng/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        
        return 6371 * c


h3_service = H3Service()
