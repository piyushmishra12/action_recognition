# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/03_models.ipynb (unless otherwise specified).

__all__ = ['Encoder', 'SimpleModel', 'simple_splitter', 'LSTM', 'ConvLSTM', 'convlstm_splitter', 'DETR', 'detr_split']

# Cell
from fastai2.vision.all import *

# Cell
@delegates(create_cnn_model)
class Encoder(Module):
    def __init__(self, arch=resnet34, n_in=3, weights_file=None, head=True, **kwargs):
        "Encoder based on resnet, if head=False returns the feature map"
        model = create_cnn_model(arch, n_out=1, n_in=n_in, pretrained=True, **kwargs)
        if weights_file is not None: load_model(weights_file, model, opt=None)
        self.body = model[0]
        if head: self.head = model[1]
        else:    self.head = nn.Sequential(*(model[1][0:3]))

    def forward(self, x):
        return self.head(self.body(x))

# Cell
class SimpleModel(Module):
    "A simple CNN model"
    def __init__(self, arch=resnet34, weights_file=None, num_classes=30, seq_len=40, debug=False):
        "Create a simple arch based model"
        model = Encoder(arch, 3, weights_file, head=False)
        nf = num_features_model(nn.Sequential(*model.body.children())) * 2
        self.encoder = model
        self.head = nn.Sequential(LinBnDrop(nf,  nf//2, p=0.2, act=nn.ReLU()),
                                  LinBnDrop(nf//2, num_classes, p=0.05))
        self.attention_layer = nn.Linear(nf, 1)
        self.debug = debug

    def forward(self, x):
        if self.debug:  print(f' input len:   {len(x), x[0].shape}')
        x = torch.stack(x, dim=1)
        if self.debug:  print(f' after stack:   {x.shape}')
        batch_size, seq_length, c, h, w = x.shape
        x = x.view(batch_size * seq_length, c, h, w)
        x = self.encoder(x)
        x = x.view(batch_size, seq_length, -1)
        if self.debug:  print(f' encoded shape: {x.shape}')
        attention_w = F.softmax(self.attention_layer(x).squeeze(-1), dim=-1)
        x = torch.sum(attention_w.unsqueeze(-1) * x, dim=1)
        if self.debug:  print(f' after attention shape: {x.shape}')
        x = self.head(x)
        return x

# Cell
def simple_splitter(model):
    return [params(model.encoder), params(model.attention_layer)+ params(model.head)]

# Cell
class LSTM(Module):
    def __init__(self, input_dim, n_hidden, n_layers, bidirectional=False, p=0.5):
        self.lstm = nn.LSTM(input_dim, n_hidden, n_layers, batch_first=True, bidirectional=bidirectional)
        self.drop = nn.Dropout(p)
        self.h = None

    def reset(self):
        self.h = None

    def forward(self, x):
        if (self.h is not None) and (x.shape[0] != self.h[0].shape[1]): #dealing with last batch on valid
#             self.h = [h_[:, :x.shape[0], :] for h_ in self.h]
            self.h = None
        raw, h = self.lstm(x, self.h)
        out = self.drop(raw)
        self.h = [h_.detach() for h_ in h]
        return out, h

# Cell
class ConvLSTM(Module):
    def __init__(self, arch=resnet34, weights_file=None, num_classes=30, lstm_layers=1, hidden_dim=1024,
                 bidirectional=True, attention=True, debug=False):
        model = Encoder(arch, 3, weights_file, head=False)
        nf = num_features_model(nn.Sequential(*model.body.children())) * 2
        self.encoder = model
        self.lstm = LSTM(nf, hidden_dim, lstm_layers, bidirectional)
        self.attention = attention
        self.attention_layer = nn.Linear(2 * hidden_dim if bidirectional else hidden_dim, 1)
        self.head = nn.Sequential(
            LinBnDrop( (lstm_layers if not attention else 1)*(2 * hidden_dim if bidirectional else hidden_dim), hidden_dim, p=0.2, act=nn.ReLU()),
            nn.Linear(hidden_dim, num_classes),
        )
        self.debug = debug

    def forward(self, x):
        x = torch.stack(x, dim=1)
        if self.debug:  print(f' after stack:   {x.shape}')
        batch_size, seq_length, c, h, w = x.shape
        x = x.view(batch_size * seq_length, c, h, w)
        x = self.encoder(x)
        if self.debug:  print(f' after encode:   {x.shape}')
        x = x.view(batch_size, seq_length, -1)
        if self.debug:  print(f' before lstm:   {x.shape}')
        x, h = self.lstm(x)
        if self.debug:  print(f' after lstm:   {x.shape}')
        if self.attention:
            attention_w = F.softmax(self.attention_layer(x).squeeze(-1), dim=-1)
            out = torch.sum(attention_w.unsqueeze(-1) * x, dim=1)
            if self.debug: print(f' after attention: {out.shape}')
        else:
            if self.debug: print(f' hidden state: {h[0].shape}')
            out = h[0].permute(1,0,2).flatten(1)
            if self.debug: print(f' hidden state flat: {out.shape}')
        return self.head(out)

    def reset(self): self.lstm.reset()

# Cell
def convlstm_splitter(model):
    return [params(model.encoder), params(model.lstm) + params(model.attention_layer) + params(model.head)]

# Cell
class DETR(Module):
    def __init__(self,  arch=resnet34, n_in=3, n_classes=30, hidden_dim=256, nheads=4, num_encoder_layers=4,
                 num_decoder_layers=4, debug=False):
        self.debug = debug

        #the image encoder
        self.backbone = Encoder(arch, n_in=n_in, head=False).body

        # create conversion layer
        self.conv = nn.Conv2d(512, hidden_dim, 1)

        # create a default PyTorch transformer
        self.transformer = nn.Transformer(
            hidden_dim, nheads, num_encoder_layers, num_decoder_layers)

        # output positional encodings (object queries)
        self.query_pos = nn.Parameter(torch.rand(1, hidden_dim))

        # spatial positional encodings
        # note that in baseline DETR we use sine positional encodings
#         self.pos = nn.Parameter(torch.rand(n, hidden_dim))
        self.row_embed = nn.Parameter(torch.rand(50, hidden_dim // 4))
        self.col_embed = nn.Parameter(torch.rand(50, hidden_dim // 4))
        self.time_embed =nn.Parameter(torch.rand(50, hidden_dim // 2))

        #head
        self.lin = nn.Linear(hidden_dim,n_classes)  #hardcodeed

    def forward(self, x):
        x = torch.stack(x, dim=1)
        if self.debug:  print(f' after stack:   {x.shape}')
        batch_size, seq_length, c, h, w = x.shape
        x = x.view(batch_size * seq_length, c, h, w)
        # propagate inputs through ResNet up to avg-pool layer
        x = self.backbone(x)
        if self.debug: print(f'backbone: {x.shape}')

        # convert from the latent dim to 256 feature planes for the transformer
        h = self.conv(x)
        if self.debug: print(f'h: {h.shape}')
        h = h.view(batch_size, seq_length, *h.shape[1:])
        if self.debug: print(f'h: {h.shape}')

        # construct positional encodings
        H, W = h.shape[-2:]
        T = h.shape[1]
        if self.debug: print(f'T,H,W: {T}, {H}, {W}')

        pos = torch.cat([
            self.time_embed[:T].view(T,1,1,-1).repeat(1, H, W, 1),
            self.col_embed[:W].view(1,1,W,-1).repeat(T, H, 1, 1),
            self.row_embed[:H].view(1,H,1,-1).repeat(T, 1, W, 1),
        ], dim=-1).flatten(0, 2).unsqueeze(1)
#         pos = self.pos.unsqueeze(1)
        if self.debug: print(f'pos: {pos.shape}')

        # propagate through the transformer
        tf_input = pos + 0.5 * h.permute(0,2,1,3,4).flatten(2).permute(2,0,1)
        if self.debug: print(f'tf_input: {tf_input.shape}')
        h = self.transformer(tf_input,
                             self.query_pos.unsqueeze(1))
        if self.debug: print(f'tf_out: {h.shape}')
        return self.lin(h).squeeze(1)


# Cell
def detr_split(m):
    return [params(m.backbone),
            params(m.conv)+params(m.transformer)+[m.query_pos]+[m.col_embed]+[m.row_embed]+[m.time_embed]+params(m.lin)]