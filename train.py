import csv
import torch
import argparse
import warnings
import numpy as np
import torch.nn as nn
import torch.utils.data
import torch.optim as optim
from model import Net, FocalLoss
from datetime import datetime
from sklearn.metrics import classification_report, accuracy_score, confusion_matrix
from plot import save_acc, save_loss, save_confusion_matrix
from data import prepare_data, load_data
from utils import *


def eval_model_train(model, trainLoader, tra_acc_list):
    y_true, y_pred = [], []
    with torch.no_grad():
        for data in trainLoader:
            inputs, labels = toCUDA(data["mel"]), toCUDA(data["label"])
            outputs = model.forward(inputs)
            predicted = torch.max(outputs.data, 1)[1]
            y_true.extend(labels.tolist())
            y_pred.extend(predicted.tolist())

    acc = 100.0 * accuracy_score(y_true, y_pred)
    print(f"Training acc   : {str(round(acc, 2))}%")
    tra_acc_list.append(acc)


def eval_model_valid(model, validationLoader, val_acc_list):
    y_true, y_pred = [], []
    with torch.no_grad():
        for data in validationLoader:
            inputs, labels = toCUDA(data["mel"]), toCUDA(data["label"])
            outputs = model.forward(inputs)
            predicted = torch.max(outputs.data, 1)[1]
            y_true.extend(labels.tolist())
            y_pred.extend(predicted.tolist())

    acc = 100.0 * accuracy_score(y_true, y_pred)
    print(f"Validation acc : {str(round(acc, 2))}%")
    val_acc_list.append(acc)


def eval_model_test(model, testLoader, classes):
    y_true, y_pred = [], []
    with torch.no_grad():
        for data in testLoader:
            inputs, labels = toCUDA(data["mel"]), toCUDA(data["label"])
            outputs = model.forward(inputs)
            predicted = torch.max(outputs.data, 1)[1]
            y_true.extend(labels.tolist())
            y_pred.extend(predicted.tolist())

    report = classification_report(y_true, y_pred, target_names=classes, digits=3)
    cm = confusion_matrix(y_true, y_pred, normalize="all")

    return report, cm


def save_log(start_time, finish_time, cls_report, cm, log_dir, classes):
    logs = f"""
Backbone     : {args.model}
Start time   : {time_stamp(start_time)}"
Finish time  : {time_stamp(finish_time)}"
Time cost    : {str((finish_time - start_time).seconds)}s"
Full finetune: {str(args.fullfinetune)}"
Focal loss   : {str(args.fl)}"""

    with open(f"{log_dir}/result.log", "w", encoding="utf-8") as f:
        f.write(cls_report + "\n")
        f.write(logs + "\n")
    f.close()

    # save confusion_matrix
    np.savetxt(f"{log_dir}/mat.csv", cm, delimiter=",")
    save_confusion_matrix(cm, classes, log_dir)

    print(cls_report)
    print("Confusion matrix :")
    print(str(cm.round(3)) + "\n")
    print(logs)


def save_history(
    model,
    tra_acc_list,
    val_acc_list,
    loss_list,
    lr_list,
    cls_report,
    cm,
    start_time,
    finish_time,
    classes,
):
    create_dir(results_dir)
    log_dir = f"{results_dir}/{args.model}__{time_stamp()}"
    create_dir(log_dir)

    acc_len = len(tra_acc_list)
    with open(f"{log_dir}/acc.csv", "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["tra_acc_list", "val_acc_list", "lr_list"])
        for i in range(acc_len):
            writer.writerow([tra_acc_list[i], val_acc_list[i], lr_list[i]])

    loss_len = len(loss_list)
    with open(f"{log_dir}/loss.csv", "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["loss_list"])
        for i in range(loss_len):
            writer.writerow([loss_list[i]])

    torch.save(model.state_dict(), f"{log_dir}/save.pt")
    print("Model saved.")

    save_acc(tra_acc_list, val_acc_list, log_dir)
    save_loss(loss_list, log_dir)
    save_log(start_time, finish_time, cls_report, cm, log_dir, classes)


def train(backbone_ver="squeezenet1_1", epoch_num=40, iteration=10, lr=0.001):
    # device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tra_acc_list, val_acc_list, loss_list, lr_list = [], [], [], []

    # load data
    ds, classes, num_samples, use_hf = prepare_data(args.fl)
    cls_num = len(classes)

    # init model
    model = Net(cls_num, m_ver=backbone_ver, full_finetune=args.fullfinetune)
    input_size = model._get_insize()
    traLoader, valLoader, tesLoader = load_data(ds, input_size, use_hf)

    # optimizer and loss
    criterion = FocalLoss(num_samples) if args.fl else nn.CrossEntropyLoss()
    optimizer = optim.SGD(model.parameters(), lr, momentum=0.9)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer,
        mode="min",
        factor=0.1,
        patience=5,
        verbose=True,
        threshold=lr,
        threshold_mode="rel",
        cooldown=0,
        min_lr=0,
        eps=1e-08,
    )

    # gpu
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        criterion = criterion.cuda()
        for state in optimizer.state.values():
            for k, v in state.items():
                if isinstance(v, torch.Tensor):
                    state[k] = v.cuda()

    # train process
    start_time = datetime.now()
    print(f"Start training [{args.model}] at {time_stamp(start_time)}")
    # loop over the dataset multiple times
    for epoch in range(epoch_num):
        epoch_str = f" Epoch {epoch + 1}/{epoch_num} "
        lr_str = optimizer.param_groups[0]["lr"]
        lr_list.append(lr_str)
        print(f"{epoch_str:-^40s}")
        print(f"Learning rate: {lr_str}")
        running_loss = 0.0
        with tqdm(total=len(traLoader), unit="batch") as pbar:
            for i, data in enumerate(traLoader, 0):
                # get the inputs
                inputs, labels = toCUDA(data["mel"]), toCUDA(data["label"])
                # zero the parameter gradients
                optimizer.zero_grad()
                # forward + backward + optimize
                outputs = model.forward(inputs)
                loss: torch.Tensor = criterion(outputs, labels)
                loss.backward()
                optimizer.step()
                # print statistics
                running_loss += loss.item()
                # print every 2000 mini-batches
                if i % iteration == iteration - 1:
                    pbar.set_description(
                        "epoch=%d/%d, lr=%.4f, loss=%.4f"
                        % (
                            epoch + 1,
                            epoch_num,
                            lr,
                            running_loss / iteration,
                        )
                    )
                    loss_list.append(running_loss / iteration)

                running_loss = 0.0

            eval_model_train(model, traLoader, tra_acc_list)
            eval_model_valid(model, valLoader, val_acc_list)
            scheduler.step(loss.item())

    finish_time = datetime.now()
    cls_report, cm = eval_model_test(model, tesLoader, classes)
    save_history(
        model,
        tra_acc_list,
        val_acc_list,
        loss_list,
        lr_list,
        cls_report,
        cm,
        start_time,
        finish_time,
        classes,
    )


if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    parser = argparse.ArgumentParser(description="train")
    parser.add_argument("--model", type=str, default="squeezenet1_1")
    parser.add_argument("--fl", type=bool, default=True)
    parser.add_argument("--fullfinetune", type=bool, default=True)
    args = parser.parse_args()

    train(backbone_ver=args.model, epoch_num=40)
