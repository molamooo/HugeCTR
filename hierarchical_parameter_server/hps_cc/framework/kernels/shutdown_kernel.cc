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

#include <exception>
#include <sys/wait.h>

#include "config.h"
#include "facade.h"
#include "tensorflow/core/framework/op_kernel.h"

namespace tensorflow {

using GPUDevice = Eigen::GpuDevice;
using CPUDevice = Eigen::ThreadPoolDevice;

template <typename Device>
class Shutdown : public OpKernel {
 public:
  explicit Shutdown(OpKernelConstruction* ctx) : OpKernel(ctx) {}
  void Compute(OpKernelContext* ctx) override {
    try {
      HierarchicalParameterServer::Facade::instance()->report_cache_intersect();
      if (std::string(std::getenv("HPS_WORKER_ID")) == "0") {
        HierarchicalParameterServer::Facade::instance()->report_avg();
      }
    } catch (const std::exception& error) {
      ctx->SetStatus(errors::Aborted(error.what()));
      return;
    }

    Tensor* status_tensor = nullptr;
    OP_REQUIRES_OK(ctx, ctx->allocate_output(0, {}, &status_tensor));
    status_tensor->flat<tstring>()(0) = "OK";
  }
};

REGISTER_KERNEL_BUILDER(Name("Shutdown").Device(DEVICE_GPU).HostMemory("status"),
                        Shutdown<GPUDevice>);

extern "C" {

int wait_one_child() {
  int child_stat;
  pid_t pid = waitpid(-1, &child_stat, 0);
  if (WEXITSTATUS(child_stat) != 0) {
    std::cerr << "detect a terminated child " << pid << ", status is "
               << WEXITSTATUS(child_stat) << "\n";
    return 1;
  } else if (WIFSIGNALED(child_stat)) {
    std::cerr << "detect an abnormal terminated child, signal is " << strsignal(WTERMSIG(child_stat));
    return 1;
  } else return 0;
}

}


}  // namespace tensorflow
