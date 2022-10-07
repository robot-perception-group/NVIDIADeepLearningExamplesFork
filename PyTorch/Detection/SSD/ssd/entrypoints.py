# Copyright (c) 2021, NVIDIA CORPORATION. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import torch
import sys
import urllib.request

# from https://github.com/NVIDIA/DeepLearningExamples/blob/master/PyTorch/SpeechSynthesis/Tacotron2/inference.py
def checkpoint_from_distributed(state_dict):
    """
    Checks whether checkpoint was generated by DistributedDataParallel. DDP
    wraps model in additional "module.", it needs to be unwrapped for single
    GPU inference.
    :param state_dict: model's state dict
    """
    ret = False
    for key, _ in state_dict.items():
        if key.find('module.') != -1:
            ret = True
            break
    return ret


# from https://github.com/NVIDIA/DeepLearningExamples/blob/master/PyTorch/SpeechSynthesis/Tacotron2/inference.py
def unwrap_distributed(state_dict):
    """
    Unwraps model from DistributedDataParallel.
    DDP wraps model in additional "module.", it needs to be removed for single
    GPU inference.
    :param state_dict: model's state dict
    """
    new_state_dict = {}
    for key, value in state_dict.items():
        new_key = key.replace('module.1.', '')
        new_key = new_key.replace('module.', '')
        new_state_dict[new_key] = value
    return new_state_dict


def _download_checkpoint(checkpoint, force_reload):
    model_dir = os.path.join(torch.hub._get_torch_home(), 'checkpoints')
    if not os.path.exists(model_dir):
        os.makedirs(model_dir)
    ckpt_file = os.path.join(model_dir, os.path.basename(checkpoint))
    if not os.path.exists(ckpt_file) or force_reload:
        sys.stderr.write('Downloading checkpoint from {}\n'.format(checkpoint))
        urllib.request.urlretrieve(checkpoint, ckpt_file)
    return ckpt_file

def nvidia_ssd_processing_utils():
    import numpy as np
    import skimage
    from skimage import io, transform

    from .utils import dboxes300_coco, Encoder

    class Processing:
        @staticmethod
        def load_image(image_path):
            """Code from Loading_Pretrained_Models.ipynb - a Caffe2 tutorial"""
            img = skimage.img_as_float(io.imread(image_path))
            if len(img.shape) == 2:
                img = np.array([img, img, img]).swapaxes(0, 2)
            return img

        @staticmethod
        def rescale(img, input_height, input_width):
            """Code from Loading_Pretrained_Models.ipynb - a Caffe2 tutorial"""
            aspect = img.shape[1] / float(img.shape[0])
            if (aspect > 1):
                # landscape orientation - wide image
                res = int(aspect * input_height)
                imgScaled = transform.resize(img, (input_width, res))
            if (aspect < 1):
                # portrait orientation - tall image
                res = int(input_width / aspect)
                imgScaled = transform.resize(img, (res, input_height))
            if (aspect == 1):
                imgScaled = transform.resize(img, (input_width, input_height))
            return imgScaled

        @staticmethod
        def crop_center(img, cropx, cropy):
            """Code from Loading_Pretrained_Models.ipynb - a Caffe2 tutorial"""
            y, x, c = img.shape
            startx = x // 2 - (cropx // 2)
            starty = y // 2 - (cropy // 2)
            return img[starty:starty + cropy, startx:startx + cropx]

        @staticmethod
        def normalize(img, mean=128, std=128):
            img = (img * 256 - mean) / std
            return img

        @staticmethod
        def prepare_tensor(inputs, fp16=False):
            NHWC = np.array(inputs)
            NCHW = np.swapaxes(np.swapaxes(NHWC, 1, 3), 2, 3)
            tensor = torch.from_numpy(NCHW)
            tensor = tensor.contiguous()
            tensor = tensor.cuda()
            tensor = tensor.float()
            if fp16:
                tensor = tensor.half()
            return tensor

        @staticmethod
        def prepare_input(img_uri):
            img = Processing.load_image(img_uri)
            img = Processing.rescale(img, 300, 300)
            img = Processing.crop_center(img, 300, 300)
            img = Processing.normalize(img)
            return img

        @staticmethod
        def decode_results(predictions):
            dboxes = dboxes300_coco()
            encoder = Encoder(dboxes)
            ploc, plabel = [val.float() for val in predictions]
            results = encoder.decode_batch(ploc, plabel, criteria=0.5, max_output=20)
            return [[pred.detach().cpu().numpy() for pred in detections] for detections in results]

        @staticmethod
        def pick_best(detections, threshold=0.3):
            bboxes, classes, confidences = detections
            best = np.argwhere(confidences > threshold)[:, 0]
            return [pred[best] for pred in detections]

        @staticmethod
        def get_coco_object_dictionary():
            import os
            file_with_coco_names = "category_names.txt"

            if not os.path.exists(file_with_coco_names):
                print("Downloading COCO annotations.")
                import urllib
                import zipfile
                import json
                import shutil
                urllib.request.urlretrieve("http://images.cocodataset.org/annotations/annotations_trainval2017.zip", "cocoanno.zip")
                with zipfile.ZipFile("cocoanno.zip", "r") as f:
                    f.extractall()
                print("Downloading finished.")
                with open("annotations/instances_val2017.json", 'r') as COCO:
                    js = json.loads(COCO.read())
                class_names = [category['name'] for category in js['categories']]
                open("category_names.txt", 'w').writelines([c+"\n" for c in class_names])
                os.remove("cocoanno.zip")
                shutil.rmtree("annotations")
            else:
                class_names = open("category_names.txt").readlines()
                class_names = [c.strip() for c in class_names]
            return class_names

    return Processing()


def nvidia_ssd(pretrained=True, **kwargs):
    """Constructs an SSD300 model.
    For detailed information on model input and output, training recipies, inference and performance
    visit: github.com/NVIDIA/DeepLearningExamples and/or ngc.nvidia.com
    Args:
        pretrained (bool, True): If True, returns a model pretrained on COCO dataset.
        model_math (str, 'fp32'): returns a model in given precision ('fp32' or 'fp16')
    """

    from . import model as ssd

    fp16 = "model_math" in kwargs and kwargs["model_math"] == "fp16"
    force_reload = "force_reload" in kwargs and kwargs["force_reload"]

    m = ssd.SSD300()
    if fp16:
        m = m.half()

        def batchnorm_to_float(module):
            """Converts batch norm to FP32"""
            if isinstance(module, torch.nn.modules.batchnorm._BatchNorm):
                module.float()
            for child in module.children():
                batchnorm_to_float(child)
            return module

        m = batchnorm_to_float(m)
        
    if pretrained:
        checkpoint = 'https://keeper.mpdl.mpg.de/f/007d0d863bee44ebbac8/?dl=1'
        ckpt_file = _download_checkpoint(checkpoint, force_reload)
        ckpt = torch.load(ckpt_file)
        ckpt = ckpt['model']
        if checkpoint_from_distributed(ckpt):
            ckpt = unwrap_distributed(ckpt)
        m.load_state_dict(ckpt)
    return m
