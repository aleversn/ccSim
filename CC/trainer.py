import os
import uuid
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from transformers import get_linear_schedule_with_warmup
import numpy as np
from CC.ICCStandard import ITrainer
from CC.model import AutoModel
from CC.loader import AutoDataloader
from CC.analysis import Analysis
from tqdm import tqdm


class Trainer(ITrainer):

    def __init__(self, tokenizer, loader_name, data_path, model_name="bert",  model_type="interactive", from_pretrained=None, data_present_path="./dataset/present.json", padding_length=50, batch_size=16, batch_size_eval=64, eval_mode='dev', task_name='Sim'):
        self.loader_name = loader_name
        self.model_name = model_name
        self.model_type = model_type
        self.from_pretrained = from_pretrained
        self.data_path = data_path
        self.task_name = task_name
        self.padding_length = padding_length
        self.dataloader_init(tokenizer, loader_name, data_path, model_type,
                             data_present_path, padding_length, batch_size, batch_size_eval, eval_mode)
        self.model_init(tokenizer, model_name)
        self.analysis = Analysis()

    def model_init(self, tokenizer, model_name):
        print('AutoModel Choose Model: {}\n'.format(model_name))
        a = AutoModel(tokenizer, model_name, self.from_pretrained)
        self.model = a()

    def dataloader_init(self, tokenizer, loader_name, data_path, model_type, data_present_path, padding_length, batch_size, batch_size_eval, eval_mode):
        d = AutoDataloader(tokenizer, loader_name, data_path,
                           model_type, data_present_path, padding_length)
        self.train_loader, self.eval_loader = d(
            batch_size, batch_size_eval, eval_mode)
    
    def model_to_device(self, resume_path=None, gpu=[0]):
        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.model.cuda()
        self.model = torch.nn.DataParallel(self.model, device_ids=gpu).cuda()

        if resume_path is not None:
            print('Accessing Resume PATH: {} ...\n'.format(resume_path))
            self.model = torch.load(resume_path)
        self.model.to(device)

    def __call__(self, resume_path=None, resume_step=None, num_epochs=30, lr=5e-5, fct_loss='MSELoss', gpu=[0, 1, 2, 3], eval_call_epoch=None):
        return self.train(resume_path=resume_path, resume_step=resume_step,
                   num_epochs=num_epochs, lr=lr, fct_loss=fct_loss, gpu=gpu, eval_call_epoch=eval_call_epoch)

    def train(self, resume_path=None, resume_step=None, num_epochs=30, lr=5e-5, fct_loss='MSELoss', gpu=[0, 1, 2, 3], eval_call_epoch=None):
        self.model_to_device(resume_path=resume_path, gpu=gpu)

        optimizer = optim.Adam(self.model.parameters(), lr=lr, weight_decay=0.)
        scheduler = get_linear_schedule_with_warmup(optimizer, 190, 80000)

        current_uid = str(uuid.uuid1()).split('-')[0]

        train_step = resume_step if resume_step is not None else 0
        for epoch in range(num_epochs):
            train_count = 0
            train_loss = 0
            tp = 0
            fp = 0
            fn = 0
            tn = 0

            train_iter = tqdm(self.train_loader)
            self.model.train()

            for it in train_iter:
                for key in it.keys():
                    it[key] = self.cuda(it[key])
                
                it['padding_length'] = int(self.padding_length / 2)
                it['fct_loss'] = fct_loss
                if fct_loss in ['BCELoss', 'MSELoss']:
                    it['labels'] = it['labels'].float()

                output = self.model(**it)
                loss, pred = output['loss'], output['pred']
                loss = loss.mean()

                loss.backward()
                optimizer.step()
                scheduler.step()
                self.model.zero_grad()

                train_loss += loss.data.item()
                train_count += 1
                train_step += 1

                gold = it['labels']
                p = (pred > 0.5).float()
                tp = ((gold == 1) & (p == 1)).sum().item()
                fp = ((gold == 0) & (p == 1)).sum().item()
                fn = ((gold == 1) & (p == 0)).sum().item()
                tn = ((gold == 0) & (p == 0)).sum().item()

                train_iter.set_description(
                    'Train: {}/{}'.format(epoch + 1, num_epochs))
                train_iter.set_postfix(
                    train_loss=train_loss / train_count, train_acc=(tp + tn) / (tp + tn + fp + fn), precision=tp / (tp + fp), recall=tp / (tp + fn), f1=2 * tp / (2 * tp + fp + fn))

            self.analysis.append_train_record({
                'epoch': epoch + 1,
                'train_loss': train_loss / train_count,
                'train_acc': (tp + tn) / (tp + tn + fp + fn),
                'precision': tp / (tp + fp),
                'recall': tp / (tp + fn),
                'f1': 2 * tp / (2 * tp + fp + fn)
            })

            model_uid = self.save_model(train_step)
            if eval_call_epoch is None or eval_call_epoch(epoch):
                X, Y = self.eval(epoch)
                self.analysis.save_xy(X, Y, uid=current_uid if self.task_name is None else self.task_name, step=train_step)

            self.analysis.save_all_records(
                uid=current_uid if self.task_name is None else self.task_name)
            yield (epoch, self.analysis.train_record, self.analysis.eval_record, self.analysis.model_record, model_uid)

    def save_model(self, current_step=0):
        if self.task_name is None:
            dir = 'undefined'
        else:
            dir = self.task_name
        if not os.path.exists('./save_model/{}/{}'.format(dir, self.model_name)):
            os.makedirs('./save_model/{}/{}'.format(dir, self.model_name))
        torch.save(
            self.model, './save_model/{}/{}/{}_{}.pth'.format(dir, self.model_name, self.model_name, current_step))
        self.analysis.append_model_record(current_step)
        return current_step

    def eval(self, epoch, resume_path=None, gpu=[0]):
        if resume_path is not None:
            self.model_to_device(resume_path=resume_path, gpu=gpu)
        
        with torch.no_grad():
            eval_count = 0
            eval_loss = 0
            tp = 0
            fp = 0
            fn = 0
            tn = 0
            X = []
            Y = []

            eval_iter = tqdm(self.eval_loader)
            self.model.eval()

            for it in eval_iter:
                for key in it.keys():
                    it[key] = self.cuda(it[key])

                it['padding_length'] = int(self.padding_length / 2)

                output = self.model(**it)
                loss, pred = output['loss'], output['pred']
                loss = loss.mean()

                eval_loss += loss.data.item()
                eval_count += 1

                gold = it['labels']
                p = (pred > 0.5).float()
                X += p.long().tolist()
                Y += gold.tolist()
                tp = ((gold == 1) & (p == 1)).sum().item()
                fp = ((gold == 0) & (p == 1)).sum().item()
                fn = ((gold == 1) & (p == 0)).sum().item()
                tn = ((gold == 0) & (p == 0)).sum().item()

                eval_iter.set_description(
                    f'Eval: {epoch + 1}')
                eval_iter.set_postfix(
                    eval_loss=eval_loss / eval_count, eval_acc=(tp + tn) / (tp + tn + fp + fn), precision=tp / (tp + fp), recall=tp / (tp + fn), f1=2 * tp / (2 * tp + fp + fn))

            self.analysis.append_eval_record({
                'epoch': epoch + 1,
                'eval_loss': eval_loss / eval_count,
                'eval_acc': (tp + tn) / (tp + tn + fp + fn),
                'precision': tp / (tp + fp),
                'recall': tp / (tp + fn),
                'f1': 2 * tp / (2 * tp + fp + fn)
            })
        
        if resume_path is not None:
            self.analysis.save_xy(X, Y, uid='0' if self.task_name is None else self.task_name, step=0)
        
        return X, Y

    def cuda(self, inputX):
        if type(inputX) == tuple:
            if torch.cuda.is_available():
                result = []
                for item in inputX:
                    result.append(item.cuda())
                return result
            return inputX
        else:
            if torch.cuda.is_available():
                return inputX.cuda()
            return inputX
