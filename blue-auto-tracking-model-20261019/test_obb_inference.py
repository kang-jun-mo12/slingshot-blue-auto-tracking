import unittest
from types import SimpleNamespace
from desktop_inference import detections_from_result
from remote_inference import validate_detections
from target_policy import centering_targets


class Tensor:
    def __init__(self, data): self.data = data
    def cpu(self): return self
    def tolist(self): return self.data


def result(corners):
    return SimpleNamespace(obb=SimpleNamespace(
        xyxyxyxy=Tensor([corners]),conf=Tensor([.9]),cls=Tensor([1.])),
        boxes=None,names={0:'enemy',1:'ally'})


class OBBTests(unittest.TestCase):
    def test_obb_only_result_supplies_blue_target_and_center(self):
        detections=detections_from_result(result([[950,520],[980,540],[970,560],[940,540]]),(1920,1080))
        self.assertEqual(detections[0]['center'],[960,540])
        self.assertEqual(detections[0]['box'],[940,520,980,560])
        self.assertEqual(len(centering_targets(detections)),1)
        self.assertEqual(validate_detections(detections)[0]['center'],[960,540])

    def test_edge_clipping_does_not_shift_obb_center(self):
        detections=detections_from_result(result([[-10,20],[20,10],[30,40],[0,50]]),(1920,1080))
        self.assertEqual(detections[0]['box'],[0,10,30,50])
        self.assertEqual(detections[0]['center'],[10,30])
        self.assertNotEqual(detections[0]['center'],[15,30])

    def test_empty_obb_is_valid(self):
        obb=SimpleNamespace(xyxyxyxy=Tensor([]),conf=Tensor([]),cls=Tensor([]))
        self.assertEqual(detections_from_result(SimpleNamespace(obb=obb,boxes=None,names={}), (1920,1080)),[])

    def test_detect_model_still_works(self):
        boxes=SimpleNamespace(data=Tensor([[900,500,1020,580,.9,1]]))
        detections=detections_from_result(SimpleNamespace(obb=None,boxes=boxes,names={1:'ally'}),(1920,1080))
        self.assertEqual(detections[0]['center'],[960,540])

    def test_malformed_obb_geometry_is_rejected(self):
        good={'box':[0,10,30,50], 'confidence':.9,'class_name':'ally','class_id':1,
              'obb_corners':[[-10,20],[20,10],[30,40],[0,50]]}
        for corners in [[],[[0,0]]*3,[[float('nan'),0]]*4,[[0,0,1]]*4]:
            with self.assertRaises(ValueError):
                validate_detections([{**good,'obb_corners':corners}])
        with self.assertRaises(ValueError):
            validate_detections([{**good,'box':[1,10,30,50]}])
        good['center']=[1900,1000]
        self.assertEqual(validate_detections([good])[0]['center'],[10,30])
