from configuration import DataConfig
import json
import logging
import numpy as np
import os
import pandas as pd
from scipy import sparse
import sys

logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s]  %(message)s",
                    datefmt='%H:%M:%S-%d%m%y',
                    stream=sys.stdout)

logger = logging.getLogger(__name__)

class DataProcessing:
    def __init__(self, data_config):
        if isinstance(data_config, DataConfig):
            self.cfg = data_config
        elif isinstance(data_config, str):
            self.cfg = DataConfig(data_config)
        else:
            raise TypeError("'data_config' must be of type 'DataConfig' or 'str'.")

    def process(self):
        np.random.seed(int(self.cfg.seed))

        logger.info(f"Reading data file {self.cfg.data_path}.")

        sep = self.cfg.separator if self.cfg.separator else ','
        raw_data = pd.read_csv(self.cfg.data_path, sep=sep, header=self.cfg.header)

        if self.cfg.threshold:
            raw_data = raw_data[raw_data[raw_data.columns.values[2]] > float(self.cfg.threshold)]

        logger.info("Applying filtering.")
        imin, umin = int(self.cfg.i_min), int(self.cfg.u_min)
        raw_data, user_activity, item_popularity = self.filter(raw_data, umin, imin)

        unique_uid = user_activity.index
        idx_perm = np.random.permutation(unique_uid.size)
        unique_uid = unique_uid[idx_perm]
        n_users = unique_uid.size
        n_heldout = self.cfg.heldout

        logger.info("Calculating splits.")
        tr_users = unique_uid[:(n_users - n_heldout * 2)]
        vd_users = unique_uid[(n_users - n_heldout * 2): (n_users - n_heldout)]
        te_users = unique_uid[(n_users - n_heldout):]

        [uhead, ihead] = raw_data.columns.values[:2]
        train_data = raw_data.loc[raw_data[uhead].isin(tr_users)]
        unique_iid = pd.unique(train_data[ihead])

        logger.info("Creating validation and test set.")
        val_data = raw_data.loc[raw_data[uhead].isin(vd_users)]
        val_data = val_data.loc[val_data[ihead].isin(unique_iid)]
        test_data = raw_data.loc[raw_data[uhead].isin(te_users)]
        test_data = test_data.loc[test_data[ihead].isin(unique_iid)]

        vcnt = val_data[[uhead]].groupby(uhead, as_index=False).size()
        tcnt = test_data[[uhead]].groupby(uhead, as_index=False).size()
        val_data = val_data.loc[val_data[uhead].isin(vcnt[vcnt>=2].index)]
        test_data = test_data.loc[test_data[uhead].isin(tcnt[tcnt>=2].index)]

        val_data_tr, val_data_te = self.split_train_test(val_data)
        test_data_tr, test_data_te = self.split_train_test(test_data)

        val_us = list(val_data.groupby(uhead).count().index)
        te_us = list(test_data.groupby(uhead).count().index)
        us = val_us + te_us

        unique_uid = list(unique_uid)
        todel = [u for u in unique_uid[len(tr_users):] if u not in us]
        for u in todel:
            unique_uid.remove(u)

        self.i2id = dict((iid, i) for (i, iid) in enumerate(unique_iid))
        self.u2id = dict((uid, i) for (i, uid) in enumerate(unique_uid))

        logger.info("Saving unique_iid.txt.")
        pro_dir = self.cfg.proc_path
        if not os.path.exists(pro_dir):
            os.makedirs(pro_dir)

        with open(os.path.join(pro_dir, 'unique_iid.txt'), 'w') as f:
            for iid in unique_iid:
                f.write('%s\n' % iid)

        logger.info("Saving unique_uid.txt.")
        with open(os.path.join(pro_dir, 'unique_uid.txt'), 'w') as f:
            for uid in unique_uid:
                f.write('%s\n' % uid)

        train_data = self.numerize(train_data, self.u2id, self.i2id)
        val_data_tr = self.numerize(val_data_tr, self.u2id, self.i2id)
        val_data_te = self.numerize(val_data_te, self.u2id, self.i2id)
        test_data_tr = self.numerize(test_data_tr, self.u2id, self.i2id)
        test_data_te = self.numerize(test_data_te, self.u2id, self.i2id)

        logger.info("Saving all the files.")
        train_data.to_csv(os.path.join(pro_dir, 'train.csv'), index=False)
        val_data_tr.to_csv(os.path.join(pro_dir, 'validation_tr.csv'), index=False)
        val_data_te.to_csv(os.path.join(pro_dir, 'validation_te.csv'), index=False)
        test_data_tr.to_csv(os.path.join(pro_dir, 'test_tr.csv'), index=False)
        test_data_te.to_csv(os.path.join(pro_dir, 'test_te.csv'), index=False)
        logger.info("Preprocessing complete!")

    def filter(self, data, min_u=5, min_i=0):
        def get_count(data, id):
            return data[[id]].groupby(id, as_index=False).size()

        [uhead, ihead] = data.columns.values[:2]
        if min_i > 0:
            icnt = get_count(data, ihead)
            data = data[data[ihead].isin(icnt.index[icnt >= min_i])]

        if min_u > 0:
            ucnt = get_count(data, uhead)
            data = data[data[uhead].isin(ucnt.index[ucnt >= min_u])]

        ucnt, icnt = get_count(data, uhead), get_count(data, ihead)
        return data, ucnt, icnt

    def numerize(self, data, u2id, i2id):
        [uhead, ihead] = data.columns.values[:2]
        uid = data[uhead].apply(lambda x: u2id[x])
        iid = data[ihead].apply(lambda x: i2id[x])
        if self.cfg.topn:
            return pd.DataFrame(data={'uid': uid, 'iid': iid}, columns=['uid', 'iid'])
        else:
            dic_data = {'uid': uid, 'iid': iid}
            for c in data.columns.values[2:]:
                dic_data[c] = data[c]
            cols = ['uid', 'iid'] + list(data.columns.values[2:])
            return pd.DataFrame(data=dic_data, columns=cols)

    def split_train_test(self, data):
        np.random.seed(self.cfg.seed)
        test_prop = float(self.cfg.test_prop) if self.cfg.test_prop else 0.2
        [uhead, ihead] = data.columns.values[:2]
        data_grouped_by_user = data.groupby(uhead)
        tr_list, te_list = [], []

        for _, group in data_grouped_by_user:
            n_items_u = len(group)
            if n_items_u > 1:
                idx = np.zeros(n_items_u, dtype='bool')
                sz = max(int(test_prop * n_items_u), 1)
                idx[np.random.choice(n_items_u, size=sz, replace=False).astype('int64')] = True
                tr_list.append(group[np.logical_not(idx)])
                te_list.append(group[idx])
            else:
                logger.warning("Skipped user in test set: number of ratings <= 1.")

        data_tr = pd.concat(tr_list)
        data_te = pd.concat(te_list)
        return data_tr, data_te


class DataReader():
    def __init__(self, data_config):
        if isinstance(data_config, DataConfig):
            self.cfg = data_config
        elif isinstance(data_config, str):
            self.cfg = DataConfig(data_config)
        else:
            raise TypeError("'data_config' must be of type 'DataConfig' or 'str'.")
        self.n_items = self.load_n_items()

    def load_data(self, datatype='train'):
        if datatype == 'train':
            return self.load_train_data()
        elif datatype == 'validation':
            return self.load_train_test_data(datatype)
        elif datatype == 'test':
            return self.load_train_test_data(datatype)
        elif datatype == 'full':
            tr = self.load_train_data()
            val_tr, val_te = self.load_train_test_data("validation")
            te_tr, te_te = self.load_train_test_data("test")
            val = val_tr + val_te
            te = te_tr + te_te
            return sparse.vstack([tr, val, te])
        else:
            raise ValueError("Parameter datatype should be in ['train', 'validation', 'test', 'full']")

    def load_n_items(self):
        unique_iid = []
        with open(os.path.join(self.cfg.proc_path, 'unique_iid.txt'), 'r') as f:
            for line in f:
                unique_iid.append(line.strip())
        return len(unique_iid)

    def load_train_data(self):
        path = os.path.join(self.cfg.proc_path, 'train.csv')
        data = pd.read_csv(path)
        n_users = data['uid'].max() + 1

        rows, cols = data['uid'], data['iid']
        if self.cfg.topn:
            values = np.ones_like(rows)
        else:
            values = data[data.columns.values[2]]

        data = sparse.csr_matrix((values,
                                 (rows, cols)), dtype='float64',
                                 shape=(n_users, self.n_items))
        return data

    def load_train_test_data(self, datatype='test'):
        tr_path = os.path.join(self.cfg.proc_path, f'{datatype}_tr.csv')
        te_path = os.path.join(self.cfg.proc_path, f'{datatype}_te.csv')

        data_tr = pd.read_csv(tr_path)
        data_te = pd.read_csv(te_path)

        start_idx = min(data_tr['uid'].min(), data_te['uid'].min())
        end_idx = max(data_tr['uid'].max(), data_te['uid'].max())

        rows_tr, cols_tr = data_tr['uid'] - start_idx, data_tr['iid']
        rows_te, cols_te = data_te['uid'] - start_idx, data_te['iid']

        if self.cfg.topn:
            values_tr = np.ones_like(rows_tr)
            values_te = np.ones_like(rows_te)
        else:
            values_tr = data_tr[data_tr.columns.values[2]]
            values_te = data_te[data_tr.columns.values[2]]

        data_tr = sparse.csr_matrix((values_tr,
                                    (rows_tr, cols_tr)),
                                    dtype='float64',
                                    shape=(end_idx - start_idx + 1, self.n_items))
        data_te = sparse.csr_matrix((values_te,
                                    (rows_te, cols_te)),
                                    dtype='float64',
                                    shape=(end_idx - start_idx + 1, self.n_items))

        tr_idx = np.diff(data_tr.indptr) != 0
        te_idx = np.diff(data_te.indptr) != 0
        keep_idx = tr_idx * te_idx
        return data_tr[tr_idx], data_te[tr_idx]


class DatasetManager():
    def __init__(self, config_file):
        reader = DataReader(config_file)
        train_data = reader.load_data('train')
        vad_data_tr, vad_data_te = reader.load_data('validation')
        test_data_tr, test_data_te = reader.load_data('test')

        self.n_items = reader.n_items
        self.training_set = (train_data, None)
        self.validation_set = (vad_data_tr, vad_data_te)
        self.test_set = (test_data_tr, test_data_te)

    def get_train_and_test(self):
        tr = sparse.vstack([self.training_set[0], sum(self.validation_set), self.test_set[0]])
        te = self.test_set[1]
        return tr, te
