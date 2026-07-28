import math

import ase.io

from pathlib import Path
import os
import numpy as np


def longest_periodic():
    total_files = 145923
    p = Path("D:/Quantum Student job/training_data/mptrj-gga-ggapu")
    max_length = 0
    iterator = 1
    prev_perc = 0
    print(f"progress: {prev_perc}%")
    for path in p.iterdir():
        if path.is_file():

            dataset = ase.io.read(path, ':')
            if len(dataset) > max_length:
                max_length = len(dataset)
                print(f"new largest dataset found: {path}, length: {max_length}")

            perc = math.floor((float(iterator) / float(total_files)) * 100)
            if perc - prev_perc == 1:
                prev_perc = perc
                print(f"progress: {prev_perc}%")

            iterator += 1
            del dataset


def check_homogeneity(dpath):
    dset = ase.io.read(dpath, ':')
    formulas = set()
    print('Dataset is loaded, starting scanning...')
    for i in range(100):
        formulas.add(dset[i].get_chemical_formula())

        if len(formulas) > 1:
            print(f"different structure at index: {i}")

        print(f"progress: {i}%")

    print(f"final length: {len(formulas)}")


def get_sub_frames():
    start_path = "D:/Quantum Student job/datasets/AM26/am26.extxyz"
    dest_path = "D:/Quantum Student job/datasets/AM26/am26_subbed_100.extxyz"
    dset = ase.io.read(start_path, ':')
    """sframe = dset[:100]
    print('sampling done, initiating writing procedure:')
    ase.io.extxyz.write_extxyz()
    ase.io.write(dest_path, sframe, ".extxyz")
    print('writing done, checking homogeneity:')"""
    check_homogeneity(dest_path)
    print('homogeneity check done, checking the source and the sub-sampled datasets:')

    sframe = ase.io.read(dest_path, ':')

    for i in range(100):
        if sframe[i].get_chemical_formula() != dset[i].get_chemical_formula():
            print(
                f"inconsistency at index : {i}, subbed: {sframe[i].get_chemical_formula()}, original: {dset[i].get_chemical_formula()} ")
        if sframe[i].get_potential_energy() != dset[i].get_potential_energy():
            print(
                f"inconsistency at index : {i}, subbed: {sframe[i].get_potential_energy()}, original: {dset[i].get_potential_energy()} ")
        """if set(sframe[i].get_forces()) != set(dset[i].get_forces()):
            print(f"inconsistency at index {i} for forces")"""

    print('consistency check finished, going home bye bye')


def gather_dataset():
    p = Path("D:/Quantum Student job/training_data/mptrj-gga-ggapu")
    output_path = "D:/Quantum Student job/training_data/mptrj_sampled_over_200.extxyz"
    current_atoms_list = []
    while True:
        for path in p.iterdir():
            if path.is_file():
                if np.random.rand() < 0.3:
                    dataset = ase.io.read(path, ':')
                    current_atoms_list.extend(dataset)
                    print(f"Update in list : {len(current_atoms_list)}")

            if len(current_atoms_list) > 200:
                with open(output_path, "w") as f:
                    ase.io.extxyz.write_extxyz(f, current_atoms_list)
                    print(
                        f"Dataset sampling finished successfully, the resulting dataset has length: {len(current_atoms_list)}")
                    return


def stachyose():
    start_path = "D:/Quantum Student job/datasets/md22_stachyose/md22_stachyose.xyz"
    output_path = "D:/Quantum Student job/datasets/md22_stachyose/md22_stachyose_sampled.xyz"
    dataset = ase.io.read(start_path, '::100')
    ase.io.write(output_path, dataset)
    print(f"Dataset sampled successfully")


def graphene():
    start_path_dataset = "D:/Quantum Student job/datasets/Graphene/test.xyz"
    start_path_pred = "D:/Quantum Student job/datasets/Graphene/predict.npz"

    end_path_dataset = "D:/Quantum Student job/FFAST_NEW/examples/data/fixed-sized subsystem/graphene_sampled.xyz"
    end_path_pred = "D:/Quantum Student job/FFAST_NEW/examples/data/fixed-sized subsystem/graphene_prediction.npz"

    dataset = ase.io.read(start_path_dataset, index='::150')
    pred = np.load(start_path_pred, allow_pickle=True)
    pred_E = pred["E"][::150]
    pred_F = pred["F"][::150]
    print(f"dataset loaded ({len(dataset)})\npred loaded: F ({pred_F.shape}), E ({pred_E.shape})")

    ase.io.write(end_path_dataset, dataset)
    np.savez(end_path_pred, E=pred_E, F=pred_F)

    print("successfully sampled and saved graphene predictions")


graphene()
