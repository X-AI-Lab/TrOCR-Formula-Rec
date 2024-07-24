# -*- encoding: utf-8 -*-
# @Author: SWHL
# @Contact: liekkaskono@163.com
import os
import random
from pathlib import Path
from typing import List, Union

os.environ["CUDA_VISIBLE_DEVICES"] = "0"

import torch
from PIL import Image
from torch.utils.data import Dataset
from tqdm import tqdm
from transformers import (
    AutoTokenizer,
    Seq2SeqTrainer,
    Seq2SeqTrainingArguments,
    TrOCRProcessor,
    VisionEncoderDecoderModel,
    default_data_collator,
)


def read_txt(txt_path: Union[Path, str]) -> List[str]:
    with open(txt_path, "r", encoding="utf-8") as f:
        data = [v.rstrip("\n") for v in f]
    return data


class IAMDataset(Dataset):
    def __init__(self, data, processor, tokenizer, max_target_length=1024):
        self.data = data
        self.processor = processor
        self.tokenizer = tokenizer
        self.max_target_length = max_target_length

    def __getitem__(self, idx):
        file_name, text = self.data[idx]
        image = Image.open(file_name).convert("RGB")
        pixel_values = self.processor(image, return_tensors="pt").pixel_values
        labels = self.tokenizer(
            text,
            padding="max_length",
            max_length=self.max_target_length,
            truncation=True,
        ).input_ids

        # important: make sure that PAD tokens are ignored by the loss function
        labels = [
            label if label != self.tokenizer.pad_token_id else -100 for label in labels
        ]

        encoding = {
            "pixel_values": pixel_values.squeeze(),
            "labels": torch.tensor(labels),
        }
        return encoding

    def __len__(self):
        return len(self.data)


def get_dataset(img_dir, txt_path):
    data_info = read_txt(txt_path)
    need_data = []
    for i, one_data in enumerate(tqdm(data_info)):
        img_path = img_dir / f"{i:07d}.png"
        if img_path.exists():
            need_data.append([str(img_path), one_data])

    random.shuffle(need_data)
    return need_data


if __name__ == "__main__":
    train_dir = Path("dataset/UniMER-1M")
    train_img_dir = train_dir / "images"
    train_txt_path = train_dir / "train.txt"
    train_data = get_dataset(train_img_dir, train_txt_path)

    test_dir = Path("dataset/UniMER-Test")
    test_img_dir = test_dir / "cpe"
    test_txt_path = test_dir / "cpe.txt"
    test_data = get_dataset(test_img_dir, test_txt_path)

    max_target_length = 512

    model_name = "microsoft/trocr-small-stage1"
    processor = TrOCRProcessor.from_pretrained(model_name)
    tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=False)
    train_dataset = IAMDataset(
        data=train_data,
        processor=processor,
        tokenizer=tokenizer,
        max_target_length=max_target_length,
    )
    eval_dataset = IAMDataset(
        data=test_data,
        processor=processor,
        tokenizer=tokenizer,
        max_target_length=max_target_length,
    )

    print("Number of training examples:", len(train_dataset))
    print("Number of validation examples:", len(eval_dataset))

    encoding = train_dataset[0]
    for k, v in encoding.items():
        print(k, v.shape)

    image = Image.open(train_data[0][0]).convert("RGB")
    print(image.size)

    labels = encoding["labels"]
    labels[labels == -100] = processor.tokenizer.pad_token_id
    label_str = processor.decode(labels, skip_special_tokens=True)
    print(label_str)

    print("Loading the model")
    model = VisionEncoderDecoderModel.from_pretrained(model_name)
    model.config.decoder_start_token_id = processor.tokenizer.cls_token_id
    model.config.pad_token_id = processor.tokenizer.pad_token_id
    model.config.vocab_size = model.config.decoder.vocab_size

    # set beam search parameters
    model.config.eos_token_id = processor.tokenizer.sep_token_id
    model.config.max_length = max_target_length
    model.config.early_stopping = True

    # model.config.no_repeat_ngram_size = 2
    # model.config.length_penalty = 2.0
    model.config.num_beams = 10

    training_args = Seq2SeqTrainingArguments(
        predict_with_generate=True,
        eval_strategy="steps",
        per_device_train_batch_size=32,
        per_device_eval_batch_size=32,
        fp16=True,
        output_dir="outputs",
        logging_steps=2,
        save_steps=0.1,
        save_total_limit=1,
        eval_steps=0.1,
        report_to=["tensorboard"],
        num_train_epochs=1,
        dataloader_num_workers=4,
    )

    trainer = Seq2SeqTrainer(
        model=model,
        tokenizer=tokenizer,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=default_data_collator,
    )
    trainer.train()
    trainer.save_state()
