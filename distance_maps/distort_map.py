import cv2
from scipy.interpolate import griddata
import numpy as np
import os
from sklearn.preprocessing import MinMaxScaler

def start_grid(xdim, ydim, xmax, ymax):
    k = 0
    mat = np.zeros((xdim * ydim, 2))
    for i in range(0, xdim):
        for j in range(0, ydim):
            mat[k, 0] = int(i * (xmax / xdim))
            mat[k, 1] = int(j * (ymax / ydim))
            k += 1
    return mat

def distort_image(map_to_distort="map.png"):
    os.makedirs("img", exist_ok=True)

    IMG_DIM = 2000
    GRID_N = 9

    raw = np.load("new_coordinates.npy")
    print(f"MDS coordinates shape: {raw.shape}")

    input_coordinates = start_grid(GRID_N, GRID_N, IMG_DIM, IMG_DIM)
    print(f"Input coordinates shape: {input_coordinates.shape}")

    scaler = MinMaxScaler(feature_range=(200, IMG_DIM - 200))
    output_coordinates = scaler.fit_transform(raw)

    # Add 4 corner anchors + 4 edge midpoint anchors to both input and output
    # This forces the interpolation hull to cover the entire image
    # Input corners/edges (in pixel space)
    extra_input = np.array([
        [0,        0       ],
        [IMG_DIM,  0       ],
        [0,        IMG_DIM ],
        [IMG_DIM,  IMG_DIM ],
        [IMG_DIM//2, 0     ],
        [IMG_DIM//2, IMG_DIM],
        [0,        IMG_DIM//2],
        [IMG_DIM,  IMG_DIM//2],
    ], dtype=float)

    # Output corners/edges mirror the input exactly
    # so corners of the image stay at corners — no distortion at boundary
    extra_output = np.array([
        [0,        0       ],
        [IMG_DIM,  0       ],
        [0,        IMG_DIM ],
        [IMG_DIM,  IMG_DIM ],
        [IMG_DIM//2, 0     ],
        [IMG_DIM//2, IMG_DIM],
        [0,        IMG_DIM//2],
        [IMG_DIM,  IMG_DIM//2],
    ], dtype=float)

    # Append anchors to coordinate arrays
    input_coordinates  = np.vstack([input_coordinates,  extra_input ])
    output_coordinates = np.vstack([output_coordinates, extra_output])

    print(f"Total control points: {len(input_coordinates)}")

    grid_xy = start_grid(IMG_DIM, IMG_DIM, IMG_DIM, IMG_DIM)

    print("Step 1: Linear interpolation...")
    grid_z = griddata(
        input_coordinates,
        output_coordinates,
        grid_xy,
        method='linear'
    )

    # Safety net for any remaining NaNs
    nan_mask = np.isnan(grid_z[:, 0]) | np.isnan(grid_z[:, 1])
    if nan_mask.any():
        print(f"  Filling {nan_mask.sum()} remaining NaN pixels...")
        grid_z_nearest = griddata(
            input_coordinates,
            output_coordinates,
            grid_xy,
            method='nearest'
        )
        grid_z[nan_mask] = grid_z_nearest[nan_mask]

    map_x = np.clip(grid_z[:, 0], 0, IMG_DIM-1).reshape(IMG_DIM, IMG_DIM).astype('float32')
    map_y = np.clip(grid_z[:, 1], 0, IMG_DIM-1).reshape(IMG_DIM, IMG_DIM).astype('float32')

    orig_map = cv2.imread(map_to_distort)
    if orig_map is None:
        print(f"ERROR: Could not read {map_to_distort}")
        return

    cv2.imwrite("img/original_map_pune.png", orig_map)
    print("Saved img/original_map_pune.png")

    orig_map = cv2.resize(orig_map, (IMG_DIM, IMG_DIM))

    print("Step 3: Applying distortion...")
    distorted = cv2.remap(
        orig_map,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE
    )

    cv2.imwrite("img/distorted_map_pune.png", distorted,
                [cv2.IMWRITE_PNG_COMPRESSION, 0])
    print("Saved img/distorted_map_pune.png")

if __name__ == "__main__":
    distort_image(map_to_distort="map.png")