
import argparse
import os
import os.path as osp
import numpy as np
import mmcv
import torch
from mmcv.parallel import MMDataParallel
from mmcv.runner import load_checkpoint
from mmdet.core import wrap_fp16_model
from mmdet.datasets import build_dataloader, build_dataset
from mmdet.models import build_detector
#### upsnet libraries
from tools.config.config import config, update_config
from tools.dataset import *
import pickle


def single_gpu_test(model, data_loader, show=False):
    model.eval()
    results = []
    dataset = data_loader.dataset

    pano_results = {
        'all_names':[],
        'all_ssegs':[],
        'all_panos':[],
        'all_pano_cls_inds':[],
        'all_pano_obj_ids':[]
        }        
    prog_bar = mmcv.ProgressBar(len(dataset))

    for i, data in enumerate(data_loader):
        filename = \
            data['img_meta'][0].data[0][0]['filename'].split('/')[-1]

        with torch.no_grad():
            result = model(return_loss=False, rescale=not show, **data)
        # mask results
        results.append(result[:2])

        # panoptic results
        if len(result)>=3:
            pano_results['all_ssegs'].append(
                result[2]['fcn_outputs'].data.cpu(
                    ).numpy()[0].astype(np.uint8))
            pano_results['all_panos'].append(
                result[2]['panoptic_outputs'].data.cpu(
                    ).numpy()[0].astype(np.uint8))
            pano_results['all_pano_cls_inds'].append(
                result[2]['panoptic_cls_inds'].data.cpu().numpy())
            pano_results['all_names'].append(filename)

        batch_size = data['img'][0].size(0)
        for _ in range(batch_size):
            prog_bar.update()

    return results, pano_results


def parse_args():
    parser = argparse.ArgumentParser(description='MMDet test detector')
    parser.add_argument('config', help='test config file path')
    parser.add_argument('checkpoint', help='checkpoint file')
    parser.add_argument('--out', help='output result file')
    parser.add_argument('--dataset', type=str, default='Cityscapes')
    parser.add_argument('--load', action='store_true')
    parser.add_argument('--gpus', type=str, default='0' )
    parser.add_argument('--test_config', type=str, 
        default='configs/cityscapes/test_cityscapes_1gpu.yaml')
    args, rest = parser.parse_known_args()
    #### update config
    update_config(args.test_config)
    args = parser.parse_args()
    return args


def main():

    args = parse_args()
    gpus = [int(_) for _ in args.gpus.split(',')]
    if args.out is not None and not args.out.endswith(('.pkl', 'pickle')):
        raise ValueError("The output file must be a .pkl file.")

    cfg = mmcv.Config.fromfile(args.config)
    if cfg.get('cudnn_benchmark', False):
        torch.backedns.cudnn.benchmark = True
    cfg.model.pretrained = None
    cfg.data.test.test_mode = True
    distributed = False

    # build the dataloader
    dataset = build_dataset(cfg.data.test)
    data_loader = build_dataloader(
        dataset,
        imgs_per_gpu=1,
        workers_per_gpu=cfg.data.workers_per_gpu,
        dist=distributed,
        shuffle=False)
    
    # build the model and load checkpoint
    model = build_detector(cfg.model, 
                           train_cfg=None, 
                           test_cfg=cfg.test_cfg)
    fp16_cfg = cfg.get('fp16', None)
    if fp16_cfg is not None:
        wrap_fp16_model(model)
    checkpoint = load_checkpoint(model, 
                                 args.checkpoint, 
                                 map_location='cpu')

    # E.g., Cityscapes has 8 things CLASSES.
    if 'CLASSES' in checkpoint['meta']:
        model.CLASSES = checkpoint['meta']['CLASSES']
    else:
        model.CLASSES = dataset.CLASSES

    # If .pkl and _pano.pkl results are saved already, load = True.
    if args.load:
        outputs_mask = mmcv.load(args.out.replace('.pkl','_mask.pkl'))
        pano_pkl = args.out.replace('.pkl','_pano.pkl')
        outputs_pano = pickle.load(open(pano_pkl, 'rb'))
    else:
        model = MMDataParallel(model, device_ids=[gpus[0]])
        # args.show = False
        outputs_mask, outputs_pano = \
                single_gpu_test(model, data_loader)
        # save the outputs as .pkl files.
        with open(args.out.replace('.pkl','_mask.pkl'), 'wb') as f:
            pickle.dump(outputs_mask, f, protocol=2)
        with open(args.out.replace('.pkl','_pano.pkl'), 'wb') as f:
            pickle.dump(outputs_pano, f, protocol=2)

    # helper dataset from upsnet
    # config.dataset.dataset --> args.dataset
    # args.dataset = 'Cityscapes'
    # config.dataset.test_image_set = 'val'
    # result_path = 'val'
    eval_helper_dataset = eval(args.dataset)(
            image_sets=config.dataset.test_image_set.split('+'), 
            # image_sets=args.out.split('.pkl')[0],
            flip=False,
            result_path=args.out.split('.pkl')[0], 
            )

    # EVAL: SEMANTIC SEGMENTATION
    # *******************************************
    print("==> Semantic Segmentation PNGs will be saved at:")
    print("---", args.out.split('.pkl')[0]+'_ssegs/')
    eval_helper_dataset.evaluate_ssegs(
            outputs_pano['all_ssegs'], 
            args.out.replace('.pkl','_ssegs'), 
            outputs_pano['all_names'])

    # EVAL: IMAGE PANOPTIC SEGMENTATION
    # *******************************************
    print("==> Image Panoptic Segmentation PNGs and PQ.TXT will be saved at:")
    print("---", args.out.split('.pkl')[0]+'_pans_unified/')
    # If _pred_pans_2ch.pkl is saved already, load = True.
    if args.load:
        pans_2ch_pkl = args.out.replace('.pkl','_pred_pans_2ch.pkl')
        pred_pans_2ch = pickle.load(open(pans_2ch_pkl, 'rb'))
    else:
        pred_pans_2ch_ = eval_helper_dataset.get_unified_pan_result(
            outputs_pano['all_ssegs'],
            outputs_pano['all_panos'], 
            outputs_pano['all_pano_cls_inds'], 
            stuff_area_limit=config.test.panoptic_stuff_area_limit, 
            names=outputs_pano['all_names'])

        pred_keys = [_ for _ in pred_pans_2ch_.keys()]
        pred_keys.sort()
        pred_pans_2ch = [pred_pans_2ch_[k] for k in pred_keys]
        del pred_pans_2ch_
        with open(args.out.replace('.pkl','_pred_pans_2ch.pkl'), 'wb') as f:
            pickle.dump(pred_pans_2ch, f, protocol=2)
    
    # Evaluate IPQ
    eval_helper_dataset.evaluate_panoptic(
            pred_pans_2ch, args.out.replace('.pkl','_pans_unified'))


if __name__ == '__main__':
    main()
