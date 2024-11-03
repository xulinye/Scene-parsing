
import os, sys
import json
import glob
import numpy as np
import PIL.Image as Image
from tqdm import trange
from panopticapi.utils import IdGenerator, save_json
from city_default import CATEGORIES
import argparse


parser = argparse.ArgumentParser()
parser.add_argument('--mode', type=str, default='val', help='train/val/ test')
parser.add_argument('--root_dir', type=str, default='data/city_ext/', help='root directory')
args = parser.parse_args()

ROOT_DIR = args.root_dir
MODE = args.mode


def panoptic_video_converter():

    original_format_folder = os.path.join(ROOT_DIR, MODE, 'panoptic_inst')
    # folder to store panoptic PNGs
    out_folder = os.path.join(ROOT_DIR, MODE, 'panoptic_video')
    out_file = os.path.join(ROOT_DIR, 'panoptic_gt_%s_city_vps.json'%(MODE))
    if not os.path.isdir(out_folder):
        os.makedirs(out_folder)

    categories = CATEGORIES
    categories_dict = {el['id']: el for el in CATEGORIES}
    file_list = sorted(glob.glob(os.path.join(original_format_folder, '*.png')))
    images = []
    annotations = []
    instid2color = {}
    videos = []
    id_generator = IdGenerator(categories_dict)
    print('==> %s/panoptic_video/ ...'%(MODE)) 

    for idx in trange(len(file_list)):
        f = file_list[idx]
        original_format = np.array(Image.open(f))

        file_name = f.split('/')[-1]
        image_id = file_name.rsplit('_', 2)[0]
        video_id = image_id[:4]
        if video_id not in videos:
            videos.append(video_id)
            instid2color={}

        image_filename = file_name.replace('final_mask','newImg8bit').replace('gtFine_color','leftImg8bit')
        # image entry, id for image is its filename without extension
        images.append({"id": image_id,
                       "width": original_format.shape[1],
                       "height": original_format.shape[0],
                       "file_name": image_filename})
        pan_format = np.zeros((original_format.shape[0], original_format.shape[1], 3), dtype=np.uint8)

        l = np.unique(original_format)

        segm_info = {}
        for el in l:
            if el < 1000: 
                semantic_id = el
                is_crowd = 1
            else: 
                semantic_id = el // 1000
                is_crowd = 0
            if semantic_id not in categories_dict:
                continue
            if categories_dict[semantic_id]['isthing'] == 0:
                is_crowd = 0
            mask = (original_format == el)

            if el not in instid2color:
                segment_id, color = id_generator.get_id_and_color(semantic_id)
                instid2color[el] = (segment_id, color)
            else:
                segment_id, color = instid2color[el]

            pan_format[mask] = color
            # area = np.sum(mask) # segment area computation
            # # bbox computation for a segment
            # hor = np.sum(mask, axis=0)
            # hor_idx = np.nonzero(hor)[0]
            # x = hor_idx[0]
            # width = hor_idx[-1] - x + 1
            # vert = np.sum(mask, axis=1)
            # vert_idx = np.nonzero(vert)[0]
            # y = vert_idx[0]
            # height = vert_idx[-1] - y + 1
            # bbox = [int(x), int(y), int(width), int(height)]
            segm_info[int(segment_id)] = \
                    {"id": int(segment_id),
                     "category_id": int(semantic_id),
                     # "area": int(area),
                     "iscrowd": is_crowd}
        
        Image.fromarray(pan_format).save(os.path.join(out_folder, file_name))

        # segment sanity check, area recalculation
        gt_pan = np.uint32(pan_format)
        pan_gt = gt_pan[:, :, 0] + gt_pan[:, :, 1] * 256 + gt_pan[:, :, 2] * 256 * 256       
        labels, labels_cnt = np.unique(pan_gt, return_counts=True)
        gt_labels = [_ for _ in segm_info.keys()]   
        gt_labels_set = set(gt_labels) 
        for label, area in zip(labels, labels_cnt):
            if label == 0:
                continue
            if label not in gt_labels and label > 0:
                print('png label not in json labels.')
            segm_info[label]["area"] = int(area)
            gt_labels_set.remove(label)
        if len(gt_labels_set) != 0:
            raise KeyError('remaining gt_labels json')
        
        segm_info = [v for k,v in segm_info.items()]
        annotations.append({'image_id': image_id,
                            'file_name': file_name,
                            "segments_info": segm_info})

    d = {'images': images,
         'annotations': annotations,
         'categories': categories,
        }

    save_json(d, out_file)
    print('==> Saved json file at %s'%(out_file))

if __name__ == "__main__":
    panoptic_video_converter()
