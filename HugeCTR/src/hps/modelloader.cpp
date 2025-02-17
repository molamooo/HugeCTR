/*
 * Copyright (c) 2021, NVIDIA CORPORATION.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include <fcntl.h>
#include <sys/mman.h>

#include <common.hpp>
#include <cstddef>
#include <hps/inference_utils.hpp>
#include <hps/modelloader.hpp>
#include <parser.hpp>
#include <string>
#include <unordered_set>
#include <utils.hpp>

namespace HugeCTR {

template <typename TKey, typename TValue>
RawModelLoader<TKey, TValue>::RawModelLoader() : IModelLoader() {
  HCTR_LOG_S(DEBUG, WORLD) << "Created raw model loader in local memory!" << std::endl;
  embedding_table_ = new UnifiedEmbeddingTable<TKey, TValue>();
}

namespace {

std::string GetEnv(std::string key) {
  const char* env_var_val = getenv(key.c_str());
  if (env_var_val != nullptr) {
    return std::string(env_var_val);
  } else {
    return "";
  }
}

}  // namespace

template <typename TKey, typename TValue>
void RawModelLoader<TKey, TValue>::load(const std::string& table_name, const std::string& path) {
  const std::string emb_file_prefix = path + "/";
  const std::string key_file = emb_file_prefix + "key";
  const std::string vec_file = emb_file_prefix + "emb_vector";

  if (path.find("mock_") == 0) {
    this->is_mock = true;
    size_t num_key_offset = path.find('_') + 1, dim_offset = path.find_last_of('_') + 1;
    size_t num_key = std::stoull(path.substr(num_key_offset)),
           dim = std::stoull(path.substr(dim_offset));
    HCTR_LOG_S(ERROR, WORLD) << "using mock embedding with " << num_key << " * " << dim
                             << " elements\n";
    embedding_table_->key_count = num_key;
    embedding_table_->keys.resize(num_key);

    size_t vec_file_size_in_byte = sizeof(float) * num_key * dim;
    if (GetEnv("SAMGRAPH_EMPTY_FEAT") != "") {
      size_t empty_feat_num_key = 1 << std::stoull(GetEnv("SAMGRAPH_EMPTY_FEAT"));
      vec_file_size_in_byte = sizeof(float) * empty_feat_num_key * dim;
    }
    std::string shm_name = "SAMG_FEAT_SHM";
    int fd = shm_open(shm_name.c_str(), O_CREAT | O_RDWR, S_IRUSR | S_IWUSR);
    HCTR_CHECK_HINT(fd != -1, "shm open vec file shm failed\n");
    size_t padded_size = (vec_file_size_in_byte + 0x01fffff) & ~0x01fffff;
    {
      struct stat st;
      fstat(fd, &st);
      if (st.st_size < padded_size) {
        int ret = ftruncate(fd, padded_size);
        HCTR_CHECK_HINT(ret != -1, "ftruncate vec file shm failed");
      }
    }
    embedding_table_->vectors_ptr = mmap(nullptr, padded_size, PROT_WRITE | PROT_READ, MAP_SHARED, fd, 0);
    HCTR_CHECK_HINT(embedding_table_->vectors_ptr != nullptr, "mmap vec file shm failed\n");
    embedding_table_->umap_len = vec_file_size_in_byte;

    return;
  }

  std::ifstream key_stream(key_file);
  std::ifstream vec_stream(vec_file);
  if (!key_stream.is_open() || !vec_stream.is_open()) {
    HCTR_OWN_THROW(Error_t::WrongInput, "Error: embeddings file not open for reading");
  }

  const size_t key_file_size_in_byte = std::filesystem::file_size(key_file);
  const size_t vec_file_size_in_byte = std::filesystem::file_size(vec_file);

  const size_t key_size_in_byte = sizeof(long long);
  const size_t num_key = key_file_size_in_byte / key_size_in_byte;
  embedding_table_->key_count = num_key;

  const size_t num_float_val_in_vec_file = vec_file_size_in_byte / sizeof(float);

  // The temp embedding table
  embedding_table_->keys.resize(num_key);
  if (std::is_same<TKey, long long>::value) {
    key_stream.read(reinterpret_cast<char*>(embedding_table_->keys.data()), key_file_size_in_byte);
  } else {
    std::vector<long long> i64_key_vec(num_key, 0);
    key_stream.read(reinterpret_cast<char*>(i64_key_vec.data()), key_file_size_in_byte);
    std::transform(i64_key_vec.begin(), i64_key_vec.end(), embedding_table_->keys.begin(),
                   [](long long key) { return static_cast<unsigned>(key); });
  }

  embedding_table_->vectors.resize(num_float_val_in_vec_file);
  vec_stream.read(reinterpret_cast<char*>(embedding_table_->vectors.data()), vec_file_size_in_byte);
}

template <typename TKey, typename TValue>
void RawModelLoader<TKey, TValue>::delete_table() {
  std::vector<TKey>().swap(embedding_table_->keys);
  std::vector<TValue>().swap(embedding_table_->vectors);
  std::vector<TValue>().swap(embedding_table_->meta);
  delete embedding_table_;
}

template <typename TKey, typename TValue>
void* RawModelLoader<TKey, TValue>::getkeys() {
  return embedding_table_->keys.data();
}

template <typename TKey, typename TValue>
void* RawModelLoader<TKey, TValue>::getvectors() {
  if (is_mock) {
    return embedding_table_->vectors_ptr;
  }
  return embedding_table_->vectors.data();
}

template <typename TKey, typename TValue>
void* RawModelLoader<TKey, TValue>::getmetas() {
  return embedding_table_->meta.data();
}

template <typename TKey, typename TValue>
size_t RawModelLoader<TKey, TValue>::getkeycount() {
  return embedding_table_->key_count;
}

template class RawModelLoader<long long, float>;
template class RawModelLoader<unsigned int, float>;

}  // namespace HugeCTR
