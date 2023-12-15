import os
path = os.getcwd()
os.chdir(path)

import torch
from torch import nn
import logging
# from utils import logger
# from ...utils import logger
from tqdm import tqdm
import numpy as np
from copy import deepcopy
import torch.optim as optim
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torch.optim.lr_scheduler import StepLR
from ..backbone.resnet import BiasLayer
from ...data.dataset import BatchData, Exemplar
from torchvision.transforms import Compose, Normalize, ToTensor
from torchvision import transforms
from torch.utils.data import DataLoader

import torch.optim as optim






class Model(nn.Module):
    # A model consists with a backbone and a classifier
    def __init__(self, backbone, feat_dim, num_class, device):
        super().__init__()
        self.backbone = backbone
        
        self.feat_dim = feat_dim
        self.num_class = num_class
        self.classifier = nn.Linear(feat_dim, num_class)
    
        
    def forward(self, x):
        return self.get_logits(x)
    
    def get_logits(self, x):
        logits = self.classifier(self.backbone(x)['features'])
        return logits

class bic(nn.Module):
    def __init__(self, backbone, feat_dim, num_class, **kwargs):
        super().__init__()
        #dic = {"backbone": backbone, "device": self.device}
        # device setting
        self.device = kwargs['device']
        self.backbone = backbone
        self.bias_layer1 = BiasLayer().to(self.device)
        self.bias_layer2 = BiasLayer().to(self.device)
        self.bias_layer3 = BiasLayer().to(self.device)
        self.bias_layer4 = BiasLayer().to(self.device)
        self.bias_layer5 = BiasLayer().to(self.device)
        self.bias_layers=[self.bias_layer1, self.bias_layer2, self.bias_layer3, self.bias_layer4, self.bias_layer5]
        
        

        self.model = Model(backbone, feat_dim, num_class, self.device)
        self.seen_cls = kwargs['init_cls_num']
        self.inc_cls_num  = kwargs['inc_cls_num']
        self.task_num     = kwargs['task_num']
        self.T = 0
       

        optimizer_info = kwargs['optimizer']
        optimizer_name = optimizer_info['name']
        self.optimizer_kwargs = optimizer_info['kwargs']
        self.optimizer_cls = getattr(optim, optimizer_name)
        '''
        all_params = []
        for layer in self.bias_layers:
            all_params += list(layer.parameters())
        '''
        self.bias_optimizer = self.optimizer_cls(params=self.bias_layers[self.T].parameters(), **self.optimizer_kwargs)
        self.criterion = nn.CrossEntropyLoss()
        self.previous_model = None


     
        
    def bias_forward(self, input, bias_layers):
        in1 = input[:, :20]
        in2 = input[:, 20:40]
        in3 = input[:, 40:60]
        in4 = input[:, 60:80]
        in5 = input[:, 80:100]
        out1 = bias_layers[0](in1)
        out2 = bias_layers[1](in2)
        out3 = bias_layers[2](in3)
        out4 = bias_layers[3](in4)
        out5 = bias_layers[4](in5)
        '''
        out2 = self.bias_layer2(in2)
        out3 = self.bias_layer3(in3)
        out4 = self.bias_layer4(in4)
        out5 = self.bias_layer5(in5)
        '''
        return torch.cat([out1, out2, out3, out4, out5], dim = 1)

    def inference(self, data):
        x, y = data['image'], data['label']
        x = x.to(self.device)
        y = y.to(self.device)
        
        #logits = self.network(x)
        p = self.model(x)
        p = self.bias_forward(p,self.bias_layers)
        #pred = torch.argmax(logits, dim=1)
        pred = p[:,:self.seen_cls].argmax(dim=-1)
        acc = torch.sum(pred == y).item()

        return pred, acc / x.size(0)
 
    

    def after_task(self, task_idx, buffer, train_loader, test_loaders):
        for i, layer in enumerate(self.bias_layers):
            layer.printParam(i)
        self.previous_model = deepcopy(self.model)
        self.previous_bias_layers = deepcopy(self.bias_layers)
        self.T += 1
        if self.T < self.task_num:
            self.bias_optimizer = self.optimizer_cls(params=self.bias_layers[self.T].parameters(), **self.optimizer_kwargs)
        self.seen_cls += self.inc_cls_num
        
    def stage1(self, data):
        #print("Training ... ")
        #losses = []
        #stage1 正常训练
        x, y = data['image'], data['label']
        x = x.to(self.device)
        y = y.to(self.device)
        #self.bias_optimizer = self.optimizer_cls(params=self.bias_layers[self.T].parameters(), **self.optimizer_kwargs)
        p = self.model(x)
        p = self.bias_forward(p,self.bias_layers)
        
        
        loss = self.criterion(p[:,:self.seen_cls], y)
        
        pred = torch.argmax(p[:,:self.seen_cls], dim=1)
        acc = torch.sum(pred == y).item()
        return pred, acc / x.size(0), loss

    def stage1_distill(self, data):
        #print("Training ... ")
        distill_losses = []
        ce_losses = []
        T = 2

        alpha = (self.seen_cls - 20)/ self.seen_cls
        #print("classification proportion 1-alpha = ", 1-alpha)
        
        x, y = data['image'], data['label']
        x = x.to(self.device)
        y = y.to(self.device)


        #for i, (image, label) in enumerate(tqdm(train_data)):
        #    image = image.cuda()
        #    label = label.view(-1).cuda()
        p = self.model(x)
        p = self.bias_forward(p,self.bias_layers)

        pred = torch.argmax(p[:,:self.seen_cls], dim=1)
        acc = torch.sum(pred == y).item()

        with torch.no_grad():
            pre_p = self.previous_model(x)
            pre_p = self.bias_forward(pre_p,self.previous_bias_layers)
            pre_p = F.softmax(pre_p[:,:self.seen_cls-20]/T, dim=1)
        logp = F.log_softmax(p[:,:self.seen_cls-20]/T, dim=1)
        loss_soft_target = -torch.mean(torch.sum(pre_p * logp, dim=1))
        loss_hard_target = nn.CrossEntropyLoss()(p[:,:self.seen_cls], y)
        #loss = loss_soft_target * T * T + (1-alpha) * loss_hard_target
        loss = loss_soft_target + (1-alpha) * loss_hard_target
        #optimizer.zero_grad()
        #loss.backward(retain_graph=True)
        #optimizer.step()
        distill_losses.append(loss_soft_target.item())
        ce_losses.append(loss_hard_target.item())
        #print("stage1 distill loss :", np.mean(distill_losses), "ce loss :", np.mean(ce_losses))
        

        return pred, acc / x.size(0), loss



    #def stage2(self, val_bias_data, criterion, optimizer):
    def stage2(self, val_bias_data, bias_optimizer):
        
        #print("Evaluating ... ")
        losses = []

        x, y = val_bias_data['image'], val_bias_data['label']
        x = x.to(self.device)
        y = y.to(self.device)

        #for i, (image, label) in enumerate(tqdm(val_bias_data)):
        #    image = image.cuda()
        #    label = label.view(-1).cuda()
        p = self.model(x)
        p = self.bias_forward(p,self.bias_layers)
        
        loss = self.criterion(p[:,:self.seen_cls], y)
        pred = torch.argmax(p[:,:self.seen_cls], dim=1)
        acc = torch.sum(pred == y).item()

        bias_optimizer.zero_grad()
        loss.backward()
        bias_optimizer.step()
        return pred, acc / x.size(0), loss
        #losses.append(loss.item())
        #print("stage2 loss :", np.mean(losses))

    def observe(self, data):
        if self.T > 0:
            return self.stage1_distill(data)
        else :
            return self.stage1(data)

    def bias_observe(self, data):
        return self.stage2(data, self.bias_optimizer)
