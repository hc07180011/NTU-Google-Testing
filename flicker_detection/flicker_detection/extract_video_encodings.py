import os
import av
import gc
import json
import logging
import tqdm
import cv2
import random
import numpy as np
import pandas as pd
import skvideo.io
import torchvision.transforms as transforms
from sklearn.model_selection import train_test_split
from argparse import ArgumentParser
from collections import Counter
from typing import Tuple
from mypyfunc.logger import init_logger


def get_pts(
    src: str,
    dst: str = 'pts_encodings',
    map_src: str = 'mapping.json',
) -> None:
    mapping = {
        code: num
        for num, code in json.load(open(map_src, "r")).items()
    }
    for vid in os.listdir(src):
        if os.path.exists(os.path.join(dst, "{}".format(mapping[vid.split(".mp4")[0].replace(" ", "")]))):
            continue
        fh = av.open(os.path.join(src, vid))
        video = fh.streams.video[0]
        decoded = tuple(fh.decode(video))
        print(f"{vid} duration: {float(video.duration*video.time_base)}")
        pts_interval = np.array((0,) + tuple(
            float(decoded[i+1].pts*video.time_base -
                  decoded[i].pts*video.time_base)
            for i in range(0, len(decoded)-1, 1)
        ))
        std_arr = ((pts_interval - pts_interval.mean(axis=0)) /
                   pts_interval.std(axis=0))
        np.save(os.path.join(
            dst, f'{mapping[vid.split(".mp4")[0].replace(" ","")]}.npy'), std_arr)
        gc.collect()


def test_frame_extraction(inpath: str, outpath: str) -> None:
    container = av.open(inpath)
    vidstream = container.streams.video[0]
    for frame in container.decode(video=0):
        fts = float(frame.pts*vidstream.time_base)
        frame.to_image()\
            .save("{}_{}_{:.2f}.jpg".format(outpath, int(frame.index), fts))


def flicker_chunk(
    src: str,
    dst: str,
    labels: dict
) -> None:
    for chunk in os.listdir(src):
        frame_idx, vid_name = chunk.replace(".mp4", "").split("_", 1)
        if int(frame_idx) in labels[vid_name]:
            logging.debug(
                f"{os.path.join(src, chunk)} - {os.path.join(dst, chunk)}")
            os.replace(os.path.join(src, chunk), os.path.join(dst, chunk))


def multi_flicker_storage(
    src: str,
    dst: Tuple[str, str, str, str],
    labels: dict
) -> None:
    for chunk in os.listdir(src):
        vid_name = chunk.replace(".mp4", "")
        if labels.get(vid_name):
            logging.debug(
                f"{os.path.join(src, chunk)} - {os.path.join(dst[labels[vid_name]-1], chunk)}")
            os.replace(os.path.join(src, chunk), os.path.join(
                dst[labels[vid_name]-1], chunk))


def mov_dif_aug(
    src: str,
    dst: str,
    chunk_size: int,
    shape: tuple
) -> None:
    """
    http://www.scikit-video.org/stable/io.html
    https://github.com/dmlc/decord
    https://stackoverflow.com/questions/22994189/clean-way-to-fill-third-dimension-of-numpy-array
    https://ottverse.com/change-resolution-resize-scale-video-using-ffmpeg/
    ffmpeg -i 0096.mp4 -vf scale=-1:512 frame_%d.jpg
    """
    dst_vid = [vid.split("_", 1)[1].replace(".mp4", "")
               for vid in os.listdir(dst)]
    w_chunk = np.zeros((chunk_size,)+shape, dtype=np.uint8)
    for vid in tqdm.tqdm(os.listdir(src)):
        if vid.replace(".mp4", "").replace("reduced_", "") in dst_vid:
            continue

        cur = 0
        vidcap = cv2.VideoCapture(os.path.join(src, vid))
        success, frame = vidcap.read()
        w_chunk[:] = frame
        while success:
            w_chunk[cur % chunk_size] = frame
            cur += 1
            idx = [i % chunk_size for i in range(cur-chunk_size, cur)]

            mov = np.apply_along_axis(
                lambda f: (f*(255/f.max())).astype(np.uint8),
                axis=0, arr=np.diff(w_chunk[idx], axis=0).astype(np.uint8)
            )
            w_chunk[idx] = cv2.normalize(
                w_chunk[idx],
                None,
                alpha=0,
                beta=1,
                norm_type=cv2.NORM_MINMAX,
                dtype=cv2.CV_32F
            )
            stacked = np.array([
                np.hstack((norm, mov))
                for norm, mov in zip(w_chunk[idx], mov)
            ])
            skvideo.io.vwrite(
                os.path.join(dst, f"{cur}_"+vid.replace("reduced_", "")),
                stacked
            )
            success, frame = vidcap.read()
        gc.collect()


def preprocessing(
    flicker_dir: str,
    non_flicker_dir: str,
    cache_path: str,
) -> Tuple[np.ndarray, np.ndarray]:

    if os.path.exists("/{}.npz".format(cache_path)):
        __cache__ = np.load("/{}.npz".format(cache_path), allow_pickle=True)
        return tuple(__cache__[k] for k in __cache__)

    false_positives_vid = [
        '17271FQCB00002_video_6',
        'video_0B061FQCB00136_barbet_07-07-2022_00-05-51-678',
        'video_0B061FQCB00136_barbet_07-07-2022_00-12-11-280',
        'video_0B061FQCB00136_barbet_07-21-2022_15-37-32-891',
        'video_0B061FQCB00136_barbet_07-21-2022_14-17-42-501',
        'video_03121JEC200057_sunfish_07-06-2022_23-18-35-286'
    ]
    flicker_lst = os.listdir(flicker_dir)
    non_flicker_lst = [
        x for x in os.listdir(non_flicker_dir)
        if x.replace(".mp4", "").split("_", 1)[-1] not in false_positives_vid
    ]
    fp = list(set(os.listdir(non_flicker_dir)) - set(non_flicker_lst))
    # logging.debug(fp_Test)

    random.seed(42)
    random.shuffle(non_flicker_lst)
    random.shuffle(flicker_lst)
    random.shuffle(fp)
    non_flicker_train = non_flicker_lst[:int(len(non_flicker_lst)*0.8)]
    non_flicker_test = non_flicker_lst[int(len(non_flicker_lst)*0.8):]
    flicker_train = flicker_lst[:int(len(flicker_lst)*0.8)]
    flicker_test = flicker_lst[int(len(flicker_lst)*0.8):]
    fp_train = fp[:int(len(fp)*0.8)]
    fp_test = fp[int(len(fp)*0.8):]

    length = max([
        len(fp_test),
        len(flicker_train),
        len(flicker_test),
        len(non_flicker_train+fp_train),
        len(non_flicker_test+fp_test)
    ])
    pd.DataFrame({
        "flicker_train": tuple(flicker_train) + ("",) * (length - len(flicker_train)),
        "non_flicker_train": tuple(non_flicker_train+fp_train) + ("",) * (length - len(non_flicker_train+fp_train)),
        "flicker_test": tuple(flicker_test) + ("",) * (length - len(flicker_test)),
        "non_flicker_test": tuple(non_flicker_test+fp_test) + ("",) * (length - len(non_flicker_test+fp_test)),
    }).to_csv("{}.csv".format(cache_path))

    np.savez(cache_path, flicker_train, non_flicker_train,
             fp_test, flicker_test, non_flicker_test)


def command_arg() -> ArgumentParser:
    parser = ArgumentParser()
    parser.add_argument('--label_path', type=str, default="data/new_label.json",
                        help='path of json that store the labeled frames')
    parser.add_argument('--mapping_path', type=str, default="data/mapping.json",
                        help='path of json that maps encrpypted video file name to simple naming')
    parser.add_argument('--flicker_dir', type=str, default="data/flicker-chunks",
                        help='directory of flicker videos')
    parser.add_argument('--non_flicker_dir', type=str, default="data/no_flicker",
                        help='directory of flicker videos')
    parser.add_argument('--cache_path', type=str, default=".cache/train_test",
                        help='directory of miscenllaneous information')
    parser.add_argument('--videos_path', type=str, default="data/lower_res",
                        help='src directory to extract embeddings from')
    parser.add_argument(
        "-preprocess", "--preprocess", action="store_true",
        default=False,
        help="Whether to do training"
    )
    parser.add_argument(
        "-split", "--split", action="store_true",
        default=False,
        help="Whether to do testing"
    )
    return parser.parse_args()


if __name__ == "__main__":
    """
    train end to end, integrate cnn with lstm, and do back prop for same loss function
    smaller windows of variable frame rate should have few percent performance boost
    sliding window each frame is a data point

    divide by 255 to get range of 0,1 normalization(known cv preprocess, may not affect), multiply everything by 255 to rescale it and take floor/ ceeling
    include flicker frames in non flicker video data ponts as well because testing data will not have data label
    training should be as close as possible to testing(otherwise causes domain shifts network will not perform well)

    just oversample by drawing to mini batch just make sure epochs dont have repeating minibatch
    find state of art and compare for paper

    25471 : 997

    relabel classes to beginning of flicker, inside flicker and end flicker for multiclass 
          - might improve flicker detection performance
    run simple statistics of flicker duration using labels, learn how long the flicker, get histogram counting number of flicker sequence length 

    find good reference novelty/outlier detection for video understanding, use it as reference
    https://towardsdatascience.com/how-to-make-a-pytorch-transformer-for-time-series-forecasting-69e073d4061e

     for imbalance class and for multiclass
    don't even load between flicker videos
    use decord cycle flicker, only load non flicker once
    get google resources, beause they complain about it
    Google allow to publish dataset for paper? or perform on outlier detection data
    cnn - sequence transformer
    egocentric computer vision
    independent study next semester
     Seminar in Information Science and Technology
      Predictive Modeling in Biomedicine
    """
    init_logger()
    args = command_arg()
    videos_path, label_path, mapping_path, flicker_path, non_flicker_path, cache_path = args.videos_path, args.label_path, args.mapping_path, args.flicker_dir, args.non_flicker_dir, args.cache_path
    labels = json.load(open(label_path, "r"))

    if args.preprocess:
        mov_dif_aug(
            videos_path,
            non_flicker_path,
            chunk_size=21,
            shape=(360, 180, 3)
        )

    if args.split:
        preprocessing(
            flicker_path,
            non_flicker_path,
            cache_path,
        )
    # flicker_chunk(non_flicker_path, flicker_path, labels)
    # multi_flicker_storage(
    #     flicker_path,
    #     ("data/flicker1", "data/flicker2", "data/flicker3", "data/flicker4"),
    #     json.load(open("data/multi_label.json", "r"))
    # )
