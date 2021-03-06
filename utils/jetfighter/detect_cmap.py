import os
import os.path
import argparse
import glob
import urllib
import itertools
import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
import skimage.io
import skimage.color
import matplotlib.pyplot as plt
from colorspacious import cspace_convert


RAINBOW_MAPS = ['prism', 'hsv', 'gist_rainbow',
                'rainbow', 'nipy_spectral', 'gist_ncar', 'jet']


def parse_img(fn, name=None):
    try:
        im = skimage.io.imread(fn)
    except urllib.error.HTTPError as e:
        print(fn, name)
        raise e
    if len(im.shape) == 2 or im.shape[2] < 3:
        return pd.DataFrame()
    if im.shape[2] == 4:
        im = skimage.color.rgba2rgb(im)

    im = im[np.sum(im, axis=2) != 255 * 3]
    im = im[np.sum(im, axis=1) != 0]

    if im.size == 0:
        return None

    im = pd.DataFrame.from_records(im, columns=['R', 'G', 'B'])

    col = im.groupby(im.columns.tolist()).size()
    col = col.reset_index().rename(columns={0: 'count'})

    if name is None:
        col['fn'], _ = os.path.splitext(os.path.basename(fn))
    else:
        col['fn'] = name

    return col


# Have colormaps separated into categories:
# http://matplotlib.org/examples/color/colormaps_reference.html
cmap_names = [('Perceptually Uniform Sequential', [
    'viridis', 'plasma', 'inferno', 'magma']),
              ('Sequential', [
                  'Greys', 'Purples', 'Blues', 'Greens', 'Oranges', 'Reds',
                  'YlOrBr', 'YlOrRd', 'OrRd', 'PuRd', 'RdPu', 'BuPu',
                  'GnBu', 'PuBu', 'YlGnBu', 'PuBuGn', 'BuGn', 'YlGn']),
              ('Sequential (2)', [
                  'binary', 'gist_yarg', 'gist_gray', 'gray', 'bone', 'pink',
                  'spring', 'summer', 'autumn', 'winter', 'cool', 'Wistia',
                  'hot', 'afmhot', 'gist_heat', 'copper']),
              ('Diverging', [
                  'PiYG', 'PRGn', 'BrBG', 'PuOr', 'RdGy', 'RdBu',
                  'RdYlBu', 'RdYlGn', 'Spectral', 'coolwarm', 'bwr', 'seismic']),
              ('Qualitative', [
                  'Pastel1', 'Pastel2', 'Paired', 'Accent',
                  'Dark2', 'Set1', 'Set2', 'Set3',
                  'tab10', 'tab20', 'tab20b', 'tab20c']),
              ('Miscellaneous', [
                  'flag', 'prism', 'ocean', 'gist_earth', 'terrain', 'gist_stern',
                  'gnuplot', 'gnuplot2', 'CMRmap', 'cubehelix', 'brg', 'hsv',
                  'gist_rainbow', 'rainbow', 'jet', 'nipy_spectral', 'gist_ncar'])]

# don't keep grey colormaps
drop_maps = ['Greys', 'binary', 'gist_yarg', 'gist_gray', 'gray']


def build_cmap_knn(n=256):
    """Builds a nearest neighbor graph for each colormap in matplotlib
    """
    # matplotlib.cm.ScalarMappable(cmap=plt.get_cmap('jet')).to_rgba([.1, 0.5, .9], alpha=False, bytes=True)
    cmaps = {}
    cm_names = [cat[1] for cat in cmap_names]
    for name in itertools.chain.from_iterable(cm_names):
        if name not in drop_maps:
            cm = plt.get_cmap(name)
            cmaps[name] = cm(np.linspace(0, 1, n))[:, :3]
            cmaps[name] = cspace_convert(cmaps[name], "sRGB1", "CAM02-UCS")
            cmaps[name] = NearestNeighbors(n_neighbors=1, metric='euclidean').fit(cmaps[name])
    return cmaps


CMAP_KNN = build_cmap_knn()


def convert_to_jab(df, from_cm='sRGB255', from_cols=['R', 'G', 'B']):
    """Converts a dataframe inplace from one color map to JCAM02-UCS
        Will delete originating columns
    """
    arr_tmp = cspace_convert(df[from_cols], from_cm, 'CAM02-UCS')
    df[['J', 'a', 'b']] = pd.DataFrame(arr_tmp, index=df.index)

    df.drop(columns=from_cols, inplace=True)
    return df


def find_cm_dists(df, max_diff=1.0):
    """Expects counts of colors in jab format
        find closest color in cm (in jab space):
        if diff < max_diff:
            then assume thats the mapping
            calculate difference with each remaining page color
            if less than `max_diff`,
                then assume they correspond
        calculate a % of colormap accounted data,
                    % of data accounted for by colormap
    """

    cm_stats = pd.DataFrame(index=CMAP_KNN.keys(), columns=['pct_cm', 'pct_page'])
    cm_stats.index.name = 'cm'

    for cm_name, cm_knn in CMAP_KNN.items():
        dist, idx = cm_knn.kneighbors(df[['J', 'a', 'b']])
        idx = idx[dist < max_diff]
        cm_colors = np.unique(idx)
        cm_stats.loc[cm_name, ['pct_cm', 'pct_page']] = [cm_colors.size / 256, idx.size / df.shape[0]]
    cm_stats.sort_values('pct_cm', ascending=False, inplace=True)
    return cm_stats


def detect_rainbow_from_colors(df_colors, cm_thresh=0.5, debug=None):
    """Returns a tuple of pages determined to have rainbow and
    results of colormap detection
    """
    # Write out RGB colors found
    if isinstance(debug, str):
        df_colors.to_csv(debug + '_colors.csv', index=False)

    # Find nearest color for each page
    df_colors = convert_to_jab(df_colors)
    df_cmap = df_colors.groupby('fn').apply(find_cm_dists)
    if isinstance(debug, str):
        df_cmap.to_csv(debug + '_cm.csv')

    df_cmap = df_cmap[df_cmap['pct_cm'] > cm_thresh]
    df_rainbow = df_cmap[df_cmap.index.get_level_values('cm').isin(RAINBOW_MAPS)]
    if df_rainbow.size == 0:
        return [], df_cmap

    pgs_w_rainbow = df_rainbow.index.get_level_values('fn').unique()
    if pgs_w_rainbow.str.contains('-').any():
        pgs_w_rainbow = pgs_w_rainbow.str.rsplit('-', 1).str[1]
    pgs_w_rainbow = pgs_w_rainbow.astype(int)

    return pgs_w_rainbow.tolist(), df_cmap

def detect_cmap(img_dir):
    fns = glob.glob(img_dir)
    df = pd.concat([parse_img(x) for x in fns], ignore_index=True, copy=False)
    has_rainbow, data = detect_rainbow_from_colors(df)
    return has_rainbow