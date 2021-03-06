# -*- coding: utf-8 -*-
"""
Created on Fri May 1 02:13:41 2020

@author: Anonymous Authors
"""
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, roc_auc_score,f1_score,recall_score
import heapq # for retrieval topK

from utilities import get_instances_with_random_neg_samples, get_test_instances_with_random_samples
from performance_and_fairness_measures import getHitRatio, getNDCG, differentialFairnessMultiClass, computeEDF, computeAbsoluteUnfairness

import torch
import torch.nn as nn
import torch.optim as optim
from torch.autograd import Variable
import torch.nn.functional as F

from collaborative_models import neuralCollabFilter

#%%The function below ensures that we seed all random generators with the same value to get reproducible results
def set_random_seed(state=1):
    gens = (np.random.seed, torch.manual_seed, torch.cuda.manual_seed)
    for set_state in gens:
        set_state(state)

RANDOM_STATE = 1
set_random_seed(RANDOM_STATE)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

#%% loss function for differential fairness
def criterionHinge(epsilonClass, epsilonBase):
    zeroTerm = torch.tensor(0.0).to(device)
    return torch.max(zeroTerm, (epsilonClass-epsilonBase))

#%% fine-tuning pre-trained model with user-career pairs
def fair_fine_tune_model(model,df_train, epochs, lr,batch_size,num_negatives,num_items,protectedAttributes,lamda,epsilonBase,unsqueeze=False):
    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=1e-6)
    model.train()
    
    all_user_input = torch.LongTensor(df_train['user_id'].values).to(device)
    all_item_input = torch.LongTensor(df_train['like_id'].values).to(device)
    
    for i in range(epochs):
        j = 0
        for batch_i in range(0,np.int64(np.floor(len(df_train)/batch_size))*batch_size,batch_size):
            data_batch = (df_train[batch_i:(batch_i+batch_size)]).reset_index(drop=True)
            train_user_input, train_item_input, train_ratings = get_instances_with_random_neg_samples(data_batch, num_items, num_negatives,device)
            if unsqueeze:
                train_ratings = train_ratings.unsqueeze(1)
            y_hat = model(train_user_input, train_item_input)
            loss1 = criterion(y_hat, train_ratings)
            
            predicted_probs = model(all_user_input, all_item_input)
            avg_epsilon = computeEDF(protectedAttributes,predicted_probs,num_items,all_item_input,device)
            loss2 = criterionHinge(avg_epsilon, epsilonBase)
            
            loss = loss1 + lamda*loss2
            
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            print('epoch: ', i, 'batch: ', j, 'out of: ',np.int64(np.floor(len(df_train)/batch_size)), 'average loss: ',loss.item())
            j = j+1

#%% model evaluation: hit rate and NDCG
def evaluate_fine_tune(model,df_val,top_K,random_samples, num_items):
    model.eval()
    avg_HR = np.zeros((len(df_val),top_K))
    avg_NDCG = np.zeros((len(df_val),top_K))
    
    for i in range(len(df_val)):
        test_user_input, test_item_input = get_test_instances_with_random_samples(df_val[i], random_samples,num_items,device)
        y_hat = model(test_user_input, test_item_input)
        y_hat = y_hat.cpu().detach().numpy().reshape((-1,))
        test_item_input = test_item_input.cpu().detach().numpy().reshape((-1,))
        map_item_score = {}
        for j in range(len(y_hat)):
            map_item_score[test_item_input[j]] = y_hat[j]
        for k in range(top_K):
            # Evaluate top rank list
            ranklist = heapq.nlargest(k, map_item_score, key=map_item_score.get)
            gtItem = test_item_input[0]
            avg_HR[i,k] = getHitRatio(ranklist, gtItem)
            avg_NDCG[i,k] = getNDCG(ranklist, gtItem)
    avg_HR = np.mean(avg_HR, axis = 0)
    avg_NDCG = np.mean(avg_NDCG, axis = 0)
    return avg_HR, avg_NDCG
#%%
def fairness_measures(model,df_val,num_items,protectedAttributes,subgroup):
    model.eval()
    user_input = torch.LongTensor(df_val['user_id'].values).to(device)
    item_input = torch.LongTensor(df_val['like_id'].values).to(device)
    y_hat = model(user_input, item_input)
    
    avg_epsilon = computeEDF(protectedAttributes,y_hat,num_items,item_input,device)
    U_abs = computeAbsoluteUnfairness(protectedAttributes,y_hat,num_items,item_input,device)

    avg_epsilon = avg_epsilon.cpu().detach().numpy().reshape((-1,)).item()
    U_abs = U_abs.cpu().detach().numpy().reshape((-1,)).item()
    if subgroup == 'age':
        print(f"average differential fairness for age: {avg_epsilon: .3f}")
        print(f"absolute unfairness for age: {U_abs: .3f}")
    else:
        print(f"average differential fairness for gender: {avg_epsilon: .3f}")
        print(f"absolute unfairness for gender: {U_abs: .3f}")

    return avg_epsilon, U_abs

#%% load data
train_users= pd.read_csv("train-test/train_usersID.csv",names=['user_id'])
test_users = pd.read_csv("train-test/test_usersID.csv",names=['user_id'])

train_careers= pd.read_csv("train-test/train_concentrationsID.csv",names=['like_id'])
test_careers = pd.read_csv("train-test/test_concentrationsID.csv",names=['like_id'])

train_protected_attributes= pd.read_csv("train-test/train_protectedAttributes.csv")
test_protected_attributes = pd.read_csv("train-test/test_protectedAttributes.csv")

# =============================================================================
# train_labels= pd.read_csv("train-test/train_labels.csv",names=['labels'])
# test_labels = pd.read_csv("train-test/test_labels.csv",names=['labels'])
# 
# unique_concentrations = (pd.concat([train_careers['like_id'],train_labels['labels']],axis=1)).reset_index(drop=True)
# unique_concentrations = unique_concentrations.drop_duplicates(subset='like_id', keep='first')
# 
# unique_careers = unique_concentrations.sort_values(by=['like_id']).reset_index(drop=True)
# unique_careers.to_csv('train-test/unique_careers.csv',index=False)
# =============================================================================
unique_careers= pd.read_csv("train-test/unique_careers.csv")
train_userPages = pd.read_csv("train-test/train_userPages.csv")

train_data = (pd.concat([train_users['user_id'],train_careers['like_id']],axis=1)).reset_index(drop=True)
test_data = (pd.concat([test_users['user_id'],test_careers['like_id']],axis=1)).reset_index(drop=True)

#%% set hyperparameters
emb_size = 128
hidden_layers = np.array([emb_size, 64, 32, 16])
output_size = 1
num_epochs = 10
learning_rate = 0.001
batch_size = 256 
num_negatives = 5

random_samples = 15
top_K = 10

# to load pre-train model correctly
num_uniqueUsers = len(train_userPages.user_id.unique())
num_uniqueLikes = len(train_userPages.like_id.unique())

# to fine tune career recommendation
num_uniqueCareers = len(train_data.like_id.unique())

train_gender = train_protected_attributes['gender'].values
test_gender = test_protected_attributes['gender'].values
test_binary_age = test_protected_attributes['binary_age_group'].values
test_multi_age = test_protected_attributes['multi_age_group'].values

fairness_thres = torch.tensor(0.1).to(device)
epsilonBase = torch.tensor(0.0).to(device)

#%% load pre-trained model

DF_NCF = neuralCollabFilter(num_uniqueUsers, num_uniqueLikes, emb_size, hidden_layers,output_size).to(device)

DF_NCF.load_state_dict(torch.load("trained-models/preTrained_NCF", map_location='cpu'))

DF_NCF.to(device)

#%% fine-tune to career recommendation
# replace page items with career items
DF_NCF.like_emb = nn.Embedding(num_uniqueCareers,emb_size).to(device)
# freeze user embedding
DF_NCF.user_emb.weight.requires_grad=False
# load debiased user embeddings
debias_users_embed = np.loadtxt('results/gender_debias_users_embed.txt')
# replace user embedding of the model with debiased embeddings
DF_NCF.user_emb.weight.data = torch.from_numpy(debias_users_embed.astype(np.float32)).to(device)

fair_fine_tune_model(DF_NCF,train_data, num_epochs, learning_rate,batch_size,num_negatives,num_uniqueCareers,train_gender,fairness_thres,epsilonBase, unsqueeze=True)

torch.save(DF_NCF.state_dict(), "trained-models/DF_NCF")
#%% evaluate the fine-tune model
import sys
sys.stdout=open("NFCF_output.txt","w")

HR_array, NDCG_array = [], []
for topk in [5,7,10,25]:
    HR, NDCG = [], []
    for time in range(10):
        avg_HR_fineTune, avg_NDCG_fineTune = evaluate_fine_tune(DF_NCF,test_data.values,topk,random_samples, num_uniqueCareers)
        HR.append(avg_HR_fineTune)
        NDCG.append(avg_NDCG_fineTune)
    final_HR, final_NDCG = np.mean(HR), np.mean(NDCG)
    HR_array.append(topk)
    HR_array.append(final_HR)
    NDCG_array.append(topk)
    NDCG_array.append(final_NDCG)

#%% fairness measurements
epsilon_binary_age, abs_binary_age, epsilon_multi_age, abs_multi_age, epsilon_gender, abs_gender = [], [], [], [], [], []
for i in range(10):
    avg_epsilon_gender, U_abs_gender = fairness_measures(DF_NCF,test_data,num_uniqueCareers,test_gender,'gender')
    avg_epsilon_binary_age, U_abs_binary_age = fairness_measures(DF_NCF,test_data,num_uniqueCareers,test_binary_age,'age')
    avg_epsilon_multi_age, U_abs_multi_age = fairness_measures(DF_NCF,test_data,num_uniqueCareers,test_multi_age,'age')
    epsilon_binary_age.append(avg_epsilon_binary_age)
    epsilon_multi_age.append(avg_epsilon_multi_age)
    epsilon_gender.append(avg_epsilon_gender)
    abs_binary_age.append(U_abs_binary_age)
    abs_multi_age.append(U_abs_multi_age)
    abs_gender.append(U_abs_gender)
avg_epsilon_gender = np.mean(epsilon_gender)
avg_epsilon_binary_age = np.mean(epsilon_binary_age)
avg_epsilon_multi_age = np.mean(epsilon_multi_age)
U_abs_gender = np.mean(abs_gender)
U_abs_binary_age = np.mean(abs_binary_age)
U_abs_multi_age = np.mean(abs_multi_age)

np.savetxt('results/avg_HR_NFCF.txt',[HR_array])
np.savetxt('results/avg_NDCG_NFCF.txt',[NDCG_array])
np.savetxt('results/gender/avg_epsilon_NFCF.txt',[avg_epsilon_gender])
np.savetxt('results/gender/U_abs_NFCF.txt',[U_abs_gender])
np.savetxt('results/binary_age/avg_epsilon_NFCF.txt',[avg_epsilon_binary_age])
np.savetxt('results/binary_age/U_abs_NFCF.txt',[U_abs_binary_age])
np.savetxt('results/multi_age/avg_epsilon_NFCF.txt',[avg_epsilon_multi_age])
np.savetxt('results/multi_age/U_abs_NFCF.txt',[U_abs_multi_age])


