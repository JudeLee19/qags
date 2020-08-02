# Copyright (c) 2017-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the license found in the LICENSE file in
# the root directory of this source tree. An additional grant of patent rights
# can be found in the PATENTS file in the same directory.

from .dictionary import Dictionary, TruncatedDictionary
from .fairseq_dataset import FairseqDataset
from .concat_dataset import ConcatDataset
from .indexed_dataset import IndexedDataset, IndexedCachedDataset, IndexedInMemoryDataset, IndexedRawTextDataset
from .language_pair_dataset import LanguagePairDataset
from .monolingual_dataset import MonolingualDataset
from .token_block_dataset import TokenBlockDataset
from .sentence_classification_dataset import SentenceClassificationDataset
from .sentence_pair_classification_dataset import SentencePairClassificationDataset
from .squad_dataset import SquadDataset
from .fb_bert_dataset import BertDataset
from .summerization_language_pair_dataset import SummerizationLanguagePairDataset
#from fairseq.tasks.shuffle_transformer_lm import ModifiedBertDataset
from .iterators import (
    CountingIterator,
    EpochBatchIterator,
    GroupedIterator,
    ShardedIterator,
)

__all__ = [
    'ConcatDataset',
    'CountingIterator',
    'Dictionary',
    'EpochBatchIterator',
    'FairseqDataset',
    'GroupedIterator',
    'IndexedCachedDataset',
    'IndexedDataset',
    'IndexedInMemoryDataset',
    'IndexedRawTextDataset',
    'LanguagePairDataset',
    'MonolingualDataset',
    'ShardedIterator',
    'SentenceClassificationDataset',
    'SentencePairClassificationDataset',
    'SquadDataset',
    'TokenBlockDataset',
]
