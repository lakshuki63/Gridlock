import cv2
import numpy as np
import onnxruntime as ort
from shapely.geometry import Point, Polygon
import time
import logging

logger = logging.getLogger(__name__)

class SimpleCentroidTracker:
    def __init__(self, max_disappeared=15):
        self.next_id = 0
        self.objects = {}       # id -> centroid (cx, cy)
        self.bboxes = {}        # id -> bbox [x1, y1, x2, y2]
        self.disappeared = {}   # id -> consecutive disappeared frames
        self.max_disappeared = max_disappeared

    def update(self, rects):
        if not rects:
            for oid in list(self.disappeared.keys()):
                self.disappeared[oid] += 1
                if self.disappeared[oid] > self.max_disappeared:
                    self.objects.pop(oid, None)
                    self.bboxes.pop(oid, None)
                    self.disappeared.pop(oid, None)
            return self.objects

        input_centroids = []
        for bbox in rects:
            cx = int((bbox[0] + bbox[2]) / 2)
            cy = int((bbox[1] + bbox[3]) / 2)
            input_centroids.append((cx, cy))

        if not self.objects:
            for i, centroid in enumerate(input_centroids):
                self.objects[self.next_id] = centroid
                self.bboxes[self.next_id] = rects[i]
                self.disappeared[self.next_id] = 0
                self.next_id += 1
        else:
            object_ids = list(self.objects.keys())
            object_centroids = list(self.objects.values())

            used_inputs = set()
            used_objects = set()

            for oid_idx, oid in enumerate(object_ids):
                oc = object_centroids[oid_idx]
                best_idx = -1
                min_dist = float('inf')
                for idx, ic in enumerate(input_centroids):
                    if idx in used_inputs:
                        continue
                    dist = ((oc[0] - ic[0]) ** 2 + (oc[1] - ic[1]) ** 2) ** 0.5
                    if dist < min_dist:
                        min_dist = dist
                        best_idx = idx

                if best_idx != -1 and min_dist < 120:  # Association threshold
                    self.objects[oid] = input_centroids[best_idx]
                    self.bboxes[oid] = rects[best_idx]
                    self.disappeared[oid] = 0
                    used_inputs.add(best_idx)
                    used_objects.add(oid)

            for oid in object_ids:
                if oid not in used_objects:
                    self.disappeared[oid] += 1
                    if self.disappeared[oid] > self.max_disappeared:
                        self.objects.pop(oid, None)
                        self.bboxes.pop(oid, None)
                        self.disappeared.pop(oid, None)

            for idx, ic in enumerate(input_centroids):
                if idx not in used_inputs:
                    self.objects[self.next_id] = ic
                    self.bboxes[self.next_id] = rects[idx]
                    self.disappeared[self.next_id] = 0
                    self.next_id += 1

        return self.objects


class ONNXDetector:
    def __init__(self, model_path="traffic_model.onnx"):
        # Attempt to load model
        try:
            self.session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
            logger.info(f"Loaded ONNX model: {model_path}")
        except Exception as e:
            # Fallback to yolov8n.onnx if the renamed version is not found
            logger.warning(f"Failed to load {model_path}: {e}. Retrying with yolov8n.onnx")
            self.session = ort.InferenceSession("yolov8n.onnx", providers=['CPUExecutionProvider'])
            
        self.input_name = self.session.get_inputs()[0].name
        self.tracker = SimpleCentroidTracker()
        
        # Track polygon state: id -> consecutive frames inside polygon
        self.inside_polygon_counter = {}
        # List of violations detected: (vehicle_id, timestamp_str)
        self.violations = []

    def process_frame(self, frame, polygon_coords=None):
        """
        Run inference, track centroids, and check polygon violations.
        polygon_coords: list of [x, y] coordinates
        """
        h, w = frame.shape[:2]
        # Preprocess to 640x640 float32 normalized blob
        blob = cv2.dnn.blobFromImage(frame, 1/255.0, (640, 640), swapRB=True, crop=False)
        outputs = self.session.run(None, {self.input_name: blob})
        out = outputs[0]
        
        rects = []
        confs = []
        class_ids = []
        
        # Scenario A: [batch, num_detections, 6] (assumed format)
        if len(out.shape) == 3 and out.shape[2] == 6:
            for det in out[0]:
                x1, y1, x2, y2, conf, cls_id = det
                # We filter classes representing vehicles: 2=car, 3=motorcycle, 5=bus, 7=truck (standard COCO)
                if conf > 0.35 and int(cls_id) in {2, 3, 5, 7}:
                    rects.append([float(x1), float(y1), float(x2), float(y2)])
                    confs.append(float(conf))
                    class_ids.append(int(cls_id))
                    
        # Scenario B: Standard YOLOv8 output [1, 84, 8400]
        elif len(out.shape) == 3 and out.shape[1] == 84:
            predictions = np.squeeze(out).T
            for pred in predictions:
                scores = pred[4:]
                cls_id = np.argmax(scores)
                conf = scores[cls_id]
                if conf > 0.35 and int(cls_id) in {2, 3, 5, 7}:
                    xc, yc, wb, hb = pred[:4]
                    x1 = (xc - wb / 2) / 640.0 * w
                    y1 = (yc - hb / 2) / 640.0 * h
                    x2 = (xc + wb / 2) / 640.0 * w
                    y2 = (yc + hb / 2) / 640.0 * h
                    rects.append([x1, y1, x2, y2])
                    confs.append(float(conf))
                    class_ids.append(int(cls_id))

        # Apply Non-Maximum Suppression (NMS)
        indices = cv2.dnn.NMSBoxes(rects, confs, 0.35, 0.45)
        nms_rects = []
        if len(indices) > 0:
            for idx in indices.flatten():
                nms_rects.append(rects[idx])

        # Update centroid tracker
        tracked_objects = self.tracker.update(nms_rects)

        # Polygon checking
        polygon = None
        if polygon_coords and len(polygon_coords) >= 3:
            polygon = Polygon(polygon_coords)

        annotated = frame.copy()
        
        # Draw user polygon on the frame if specified (semi-transparent blue overlay)
        if polygon_coords and len(polygon_coords) >= 3:
            pts = np.array(polygon_coords, np.int32).reshape((-1, 1, 2))
            cv2.polylines(annotated, [pts], True, (255, 0, 0), 2)
            
            # Semi-transparent polygon overlay
            overlay = annotated.copy()
            cv2.fillPoly(overlay, [pts], (255, 0, 0))
            cv2.addWeighted(overlay, 0.15, annotated, 0.85, 0, annotated)

        # Process violations and draw tracking
        current_tracked_ids = set(tracked_objects.keys())
        
        # Clean up counter for lost objects
        for oid in list(self.inside_polygon_counter.keys()):
            if oid not in current_tracked_ids:
                self.inside_polygon_counter.pop(oid, None)

        for oid, centroid in tracked_objects.items():
            bbox = self.tracker.bboxes[oid]
            x1, y1, x2, y2 = [int(c) for c in bbox]
            
            is_violating = False
            if polygon is not None:
                p = Point(centroid[0], centroid[1])
                if polygon.contains(p):
                    self.inside_polygon_counter[oid] = self.inside_polygon_counter.get(oid, 0) + 1
                    
                    if self.inside_polygon_counter[oid] > 15:
                        is_violating = True
                        timestamp_str = time.strftime("%H:%M:%S")
                        violation_record = (oid, timestamp_str)
                        if violation_record not in self.violations:
                            self.violations.append(violation_record)
                else:
                    self.inside_polygon_counter[oid] = 0

            # Draw boxes: Red if violating, Green otherwise
            color = (0, 0, 255) if is_violating else (0, 255, 0)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            
            # Draw centroid dot and ID
            cv2.circle(annotated, centroid, 4, color, -1)
            
            label = f"ID: {oid}"
            if oid in self.inside_polygon_counter and self.inside_polygon_counter[oid] > 0:
                label += f" ({self.inside_polygon_counter[oid]}/15)"
            if is_violating:
                label += " [VIOLATION]"
                
            cv2.putText(annotated, label, (x1, y1 - 7),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

        return annotated, self.violations
