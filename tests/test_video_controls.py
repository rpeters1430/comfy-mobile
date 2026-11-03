import unittest

from app.workflow_controls import ControlError, apply_controls, find_controls


def ltx():
    return {
        'w': {'class_type': 'PrimitiveInt', 'inputs': {'value': 1280}},
        'h': {'class_type': 'PrimitiveInt', 'inputs': {'value': 720}},
        's': {'class_type': 'PrimitiveInt', 'inputs': {'value': 5}},
        'f': {'class_type': 'PrimitiveInt', 'inputs': {'value': 24}},
        'hw': {'class_type': 'ComfyMathExpression', 'inputs': {'values.a': ['w', 0], 'expression': 'a/2'}},
        'hh': {'class_type': 'ComfyMathExpression', 'inputs': {'values.a': ['h', 0], 'expression': 'a/2'}},
        'n': {'class_type': 'ComfyMathExpression', 'inputs': {'values.a': ['s', 0], 'values.b': ['f', 0], 'expression': 'a * b + 1'}},
        'v': {'class_type': 'EmptyLTXVLatentVideo', 'inputs': {'width': ['hw', 0], 'height': ['hh', 0], 'length': ['n', 0], 'batch_size': 1}},
        'a': {'class_type': 'LTXVEmptyLatentAudio', 'inputs': {'frames_number': ['n', 0]}},
    }


def wan():
    return {
        'img': {'class_type': 'LoadImage', 'inputs': {'image': 'x.png'}},
        'mp': {'class_type': 'ImageScaleToTotalPixels', 'inputs': {'image': ['img', 0], 'megapixels': 0.59}},
        'size': {'class_type': 'GetImageSize', 'inputs': {'image': ['mp', 0]}},
        's': {'class_type': 'PrimitiveFloat', 'inputs': {'value': 5.0}},
        'f': {'class_type': 'PrimitiveFloat', 'inputs': {'value': 16.0}},
        'n': {'class_type': 'ComfyMathExpression', 'inputs': {'values.a': ['s', 0], 'values.b': ['f', 0], 'expression': 'floor (a * b + 1)'}},
        'v': {'class_type': 'WanImageToVideo', 'inputs': {'width': ['size', 0], 'height': ['size', 1], 'length': ['n', 0]}},
    }


class VideoControlTests(unittest.TestCase):
    def test_ltx_length_and_size_edit_the_primitives(self):
        g = ltx()
        self.assertEqual({'seconds': 5, 'fps': 24, 'width': 1280, 'height': 720},
                         {k: v for k, v in find_controls(g, {})['video'].items() if k in ('seconds', 'fps', 'width', 'height')})
        apply_controls(g, {}, {}, {}, [], video={'seconds': 8, 'width': 720, 'height': 1280})
        self.assertEqual((8, 720, 1280), (g['s']['inputs']['value'], g['w']['inputs']['value'], g['h']['inputs']['value']))
        self.assertEqual(['n', 0], g['a']['inputs']['frames_number'])  # audio stays linked to the same length

    def test_wan_quality_sets_photo_megapixels(self):
        g = wan()
        video = find_controls(g, {})['video']
        self.assertEqual((5.0, 16.0, 0.59), (video['seconds'], video['fps'], video['megapixels']))
        self.assertNotIn('width', video)
        apply_controls(g, {}, {}, {}, [], video={'seconds': 3, 'megapixels': 0.35})
        self.assertEqual((3.0, 0.35), (g['s']['inputs']['value'], g['mp']['inputs']['megapixels']))
        self.assertIsInstance(g['s']['inputs']['value'], float)

    def test_out_of_range_or_unavailable_fails(self):
        for g, video in ((ltx(), {'seconds': 0}), (ltx(), {'seconds': 2.5}), (ltx(), {'width': 1281, 'height': 720}),
                         (ltx(), {'megapixels': 0.5}), (wan(), {'width': 512, 'height': 512}), (wan(), {'megapixels': 3}),
                         (ltx(), {'fps': 30}), (ltx(), {'seconds': float('nan')}), (ltx(), {'width': float('inf'), 'height': 720})):
            with self.subTest(video=video), self.assertRaises(ControlError):
                apply_controls(g, {}, {}, {}, [], video=video)
        self.assertIsNone(find_controls({}, {})['video'])


if __name__ == '__main__':
    unittest.main()
