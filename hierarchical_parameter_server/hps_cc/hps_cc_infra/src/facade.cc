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

#include "facade.h"

#include <cstdint>
#include <vector>

#include "coll_cache_lib/timer.h"
#include "coll_cache_lib/run_config.h"

namespace HierarchicalParameterServer {

Facade::Facade() : lookup_manager_(LookupManager::Create()) {}

Facade* Facade::instance() {
  static Facade instance;
  return &instance;
}

void Facade::operator delete(void*) {
  throw std::domain_error("This pointer cannot be manually deleted.");
}

void Facade::init(const int32_t global_replica_id,
                  const char* ps_config_file, int32_t global_batch_size,
                  int32_t num_replicas_in_sync) {
  std::call_once(lookup_manager_init_once_flag_,
                 [this, ps_config_file, global_batch_size, num_replicas_in_sync, global_replica_id]() {
                   HugeCTR::parameter_server_config ps_config{ps_config_file};
                   ps_config.fix_multi_worker(global_replica_id);
                   lookup_manager_->init(ps_config, global_batch_size, num_replicas_in_sync);
                   {
                     coll_cache_lib::common::RunConfig::worker_id = global_replica_id;
                     coll_cache_lib::common::RunConfig::num_device = num_replicas_in_sync;
                     coll_cache_lib::common::RunConfig::num_global_step_per_epoch =
                         ps_config.iteration_per_epoch * coll_cache_lib::common::RunConfig::num_device;
                     coll_cache_lib::common::RunConfig::num_epoch = ps_config.epoch;
                     profiler_ = std::make_shared<coll_cache_lib::common::Profiler>();
                     current_steps_for_each_replica_.resize(coll_cache_lib::common::RunConfig::num_device, 0);
                   }
                 });
}

void Facade::forward(const char* model_name, int32_t table_id, int32_t global_replica_id,
                     const tensorflow::Tensor* values_tensor,
                     tensorflow::Tensor* emb_vector_tensor) {
  size_t num_keys = static_cast<size_t>(values_tensor->NumElements());
  size_t emb_vec_size = static_cast<size_t>(emb_vector_tensor->shape().dim_sizes().back());
  const void* values_ptr = values_tensor->data();
  void* emb_vector_ptr = emb_vector_tensor->data();
  coll_cache_lib::common::Timer t;
  lookup_manager_->forward(std::string(model_name), table_id, global_replica_id, num_keys,
                           emb_vec_size, values_ptr, emb_vector_ptr);
  this->set_step_profile_value(global_replica_id, coll_cache_lib::common::kLogL2CacheCopyTime, t.Passed());
}

void Facade::report_avg() {
  this->profiler_->ReportStepAverage(
      coll_cache_lib::common::RunConfig::num_epoch - 1,
      coll_cache_lib::common::RunConfig::num_global_step_per_epoch - 1);
  std::cout.flush();
}

}  // namespace HierarchicalParameterServer