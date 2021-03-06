import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.utils.rnn import pack_padded_sequence as pack
from torch.nn.utils.rnn import pad_packed_sequence as pad
import numpy as np


class WordSequence(nn.Module):
    def __init__(self, data):
        super(WordSequence, self).__init__()
        self.args = data

        # word
        alp_length = data.word_alphabet_size
        emb_dim = data.word_embed_dim
        hidden_dim = data.hidden_dim
        linear_dim = data.hidden_dim*2 if data.bilstm else data.hidden_dim

        if data.use_char:
            emb_dim += data.char_hidden_dim * 2
        if data.use_bert:
            emb_dim += 768
        if data.use_elmo:
            #emb_dim += data.elmo_embed_dim
            linear_dim += data.elmo_embed_dim
        if data.out_dict:
            linear_dim += data.hidden_dim*2
        if data.feature:
            for idx in range(data.feature_num):
                emb_dim += data.feature_embed_dim[idx]
        if data.word_feature_extractor == "LSTM":
            self.word_feature = nn.LSTM(emb_dim, data.hidden_dim, num_layers=data.lstm_layer,
                                        batch_first=True, bidirectional=data.bilstm)
        elif data.word_feature_extractor == "GRU":
            self.word_feature = nn.GRU(emb_dim, data.hidden_dim, num_layers=data.lstm_layer, batch_first=True,
                                       bidirectional=self.args.bilistm)
        elif data.word_feature_extractor == "CNN":
            pass
        else:
            print("Feature Extractor Error: don't support {} word feature extractor".format(
                self.args.word_feature_extractor))

        self.word_embedding = nn.Embedding(data.word_alphabet_size, data.word_embed_dim)
        self.word_embedding.weight.requires_grad = data.fine_tune
        if data.pretrain:
            self.word_embedding.weight.data.copy_(torch.from_numpy(data.pretrain_word_embedding))
        else:
            self.word_embedding.weight.data.copy_(
                torch.from_numpy(self.random_embedding(data.word_alphabet_size, data.word_embed_dim)))

        # elmo
        if data.use_elmo:
            self.elmo_embedding = nn.Embedding(data.word_alphabet_size, data.elmo_embed_dim)
            #elmo fine_tune
            self.elmo_embedding.weight.requires_grad = data.elmo_fine_tune
            if data.pretrain_elmo_embedding is not None:
                self.elmo_embedding.weight.data.copy_(torch.from_numpy(data.pretrain_elmo_embedding))
            else:
                self.elmo_embedding.weight.data.copy_(
                    torch.from_numpy(self.random_embedding(data.word_alphabet_size, data.elmo_embed_dim)))

        # char
        if data.use_char:
            # if data.bilstm:
            #     hidden_dim += self.args.char_hidden_dim
            # hidden_dim += self.args.char_hidden_dim
            self.char_embedding = nn.Embedding(data.char_alphabet_size, data.char_embed_dim)
            self.char_embedding.weight.requires_grad = data.fine_tune
            if data.pretrain_char_embedding is not None:
                self.char_embedding.weight.data.copy_(torch.from_numpy(data.pretrain_char_embedding))
            else:
                self.char_embedding.weight.data.copy_(
                    torch.from_numpy(self.random_embedding(data.char_alphabet_size, data.char_embed_dim)))
            if data.char_feature_extractor == "LSTM":
                self.char_feature = nn.LSTM(data.char_embed_dim, data.char_hidden_dim, num_layers=1,
                                            batch_first=True, bidirectional=data.bilstm)
            if data.char_feature_extractor == "GRU":
                self.char_feature = nn.GRU(data.char_embed_dim, data.char_hidden_dim, num_layers=1,
                                           batch_first=True, bidirectional=data.bilstm)

        self.char_drop = nn.Dropout(data.dropout)
        self.word_drop = nn.Dropout(data.dropout)
        self.drop = nn.Dropout(data.dropout)
        self.att2tag = nn.Linear(1, data.label_alphabet_size)
        self.hidden2tag = nn.Linear(linear_dim, data.label_alphabet_size)
        self.scalar_parameters = nn.Parameter(torch.zeros(4))
        self.gamma = nn.Parameter(torch.tensor(1.0))

        # attention
        if data.attention:
            self.attn1 = nn.Linear(data.word_embed_dim, data.attention_dim)
            if data.bilstm:
                self.attn2 = nn.Linear(data.char_hidden_dim * 2, data.attention_dim, bias=False)
            else:
                self.attn2 = nn.Linear(data.char_hidden_dim, data.attention_dim, bias=False)
            self.attn3 = nn.Linear(data.attention_dim, data.attention_dim, bias=False)
            self.word_feature = nn.LSTM(data.attention_dim, data.hidden_dim, num_layers=data.lstm_layer,
                                        batch_first=True, bidirectional=data.bilstm)

        if data.lstm_attention:
            self.att1 = nn.Linear(data.hidden_dim * 2, data.attention_dim)
            self.softmax = nn.Softmax(dim=-1)
            self.att2 = nn.Linear(data.attention_dim, data.hidden_dim * 2, bias=False)
            self.attention = SelfAttention(data.hidden_dim * 2)
            # self.attention  = SelfAttention(data.word_embed_dim)
            # self.attention = AttentionM(data.hidden_dim*2)

        # feat
        if data.feature:
            self.feature_num = data.feature_num
            self.feature_embeddings = nn.ModuleList()
            for idx in range(self.feature_num):
                self.feature_embeddings.append(
                    nn.Embedding(data.feature_alphabets[idx].size(), data.feature_embed_dim[idx]))
            for idx in range(self.feature_num):
                if data.pretrain_feature_embedding[idx] is not None:
                    self.feature_embeddings[idx].weight.data.copy_(
                        torch.from_numpy(data.pretrain_feature_embedding[idx]))
                else:
                    self.feature_embeddings[idx].weight.data.copy_(torch.from_numpy(
                        self.random_embedding(data.feature_alphabets[idx].size(), data.feature_embed_dim[idx])))

        #out_dict
        if data.out_dict:
            self.dict_feature = nn.LSTM(8,data.hidden_dim,num_layers=1,batch_first=True,bidirectional=data.bilstm)
            self.dict_fc = nn.Linear(8,data.hidden_dim*2,bias=True)

        #LSTM weight init
        #nn.init.orthogonal(self.word_feature.all_weights[0][0],gain=0.25)
        #nn.init.orthogonal(self.word_feature.all_weights[0][1],gain=0.25)
        #nn.init.orthogonal(self.word_feature.all_weights[1][0],gain=0.25)
        #nn.init.orthogonal(self.word_feature.all_weights[1][1],gain=0.25)

    def random_embedding(self, vocab_size, embedding_dim):
        pretrain_emb = np.empty([vocab_size, embedding_dim])
        scale = np.sqrt(3.0 / embedding_dim)
        for index in range(vocab_size):
            pretrain_emb[index, :] = np.random.uniform(-scale, scale, [1, embedding_dim])
        return pretrain_emb

    def forward(self, word_inputs, feat_inputs, word_seq_length, char_inputs, char_seq_length, char_recover,dict_inputs,mask,batch_bert):
        """

             word_inputs: (batch_size,seq_len)
             word_seq_length:()
        """
        batch_size = word_inputs.size(0)
        seq_len = word_inputs.size(1)
        word_emb = self.word_embedding(word_inputs)
        if self.args.use_elmo:
            elmo_emb = self.elmo_embedding(word_inputs)
        # if self.args.use_bert:
        #     word_emb = torch.cat((word_emb,torch.squeeze(batch_bert,2)),2)
        #elmo_emb = self.drop(elmo_emb)

        # word_rep = word_emb
        if self.args.use_char:
            size = char_inputs.size(0)
            char_emb = self.char_embedding(char_inputs)
            char_emb = pack(char_emb, char_seq_length.cpu().numpy(), batch_first=True)
            char_lstm_out, char_hidden = self.char_feature(char_emb)
            char_lstm_out = pad(char_lstm_out, batch_first=True)
            char_hidden = char_hidden[0].transpose(1, 0).contiguous().view(size, -1)
            char_hidden = char_hidden[char_recover]
            char_hidden = char_hidden.view(batch_size, seq_len, -1)
            if self.args.attention:
                word_rep = F.tanh(self.attn1(word_emb) + self.attn2(char_hidden))
                z = F.sigmoid(self.attn3(word_rep))
                x = 1 - z
                word_rep = F.mul(z, word_emb) + F.mul(x, char_hidden)
            else:
                word_rep = torch.cat((word_emb, char_hidden), 2)
                word_rep = self.word_drop(word_rep)   #word represent dropout
        #if self.args.use_elmo:
        #    word_rep = torch.cat((word_rep, elmo_emb), 2)
        if self.args.feature:
            for idx in range(self.feature_num):
                word_rep = torch.cat((word_rep,self.feature_embeddings[idx](feat_inputs[idx])),2)
        # batch_bert = torch.split(batch_bert,1,dim=2)
        # normed_weights = F.softmax(self.scalar_parameters, dim=0)
        # y = self.gamma * sum(weight * tensor.squeeze(2) for weight, tensor in zip(normed_weights,batch_bert))

        # x = F.softmax(torch.mean(batch_bert,dim=2))
        x = F.softmax(torch.mean(batch_bert,dim=2))
        if self.args.use_bert:
            word_rep = torch.cat((word_rep,x),2)
        word_rep = pack(word_rep, word_seq_length.cpu().numpy(), batch_first=True)
        out, hidden = self.word_feature(word_rep)
        out, _ = pad(out, batch_first=True)
        if self.args.use_elmo:
            out = torch.cat((out,elmo_emb),2)
        if self.args.out_dict:
            dict_rep = pack(dict_inputs,word_seq_length.cpu().numpy(),batch_first=True)
            dict_out,hidden = self.dict_feature(dict_rep)
            dict_out,_=pad(dict_out,batch_first=True)
            #dict_out = self.dict_fc(dict_inputs)
            out = torch.cat((out,dict_out),2)
        if self.args.lstm_attention:

            out_list, weight_list = [], []
            for idx in range(seq_len):
                # slice_out = out[:,0:idx+1,:]
                if idx + 2 > seq_len:
                    slice_out = out
                else:
                    slice_out = out[:, 0:idx + 2, :]
                # slice_out = out
                slice_out, weights = self.attention(slice_out)
                # slice_out, weights = SelfAttention(self.args.hidden_dim*2).forward(slice_out)
                out_list.append(slice_out.unsqueeze(1))
                weight_list.append(weights)
            out = torch.cat(out_list, dim=1)
        out = self.drop(out)
        out = self.hidden2tag(out)
        return out
