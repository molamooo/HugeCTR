"""
 Copyright (c) 2022, NVIDIA CORPORATION.
 
 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at
 
     http://www.apache.org/licenses/LICENSE-2.0
 
 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
"""

import tensorflow as tf
from tensorflow.python.framework import ops
from tensorflow.python.ops import array_ops
from tensorflow.python.ops.resource_variable_ops import ResourceVariable, variable_accessed
from tensorflow.python.eager import context
from tensorflow.python.ops import resource_variable_ops

from sparse_operation_kit.experiment import raw_ops as dynamic_variable_ops
from sparse_operation_kit.experiment.communication import num_gpus


class DynamicVariable(ResourceVariable):
    def __init__(
        self,
        dimension,
        initializer=None,
        var_type=None,
        name=None,
        constraint=None,
        trainable=True,
        key_type=None,
        dtype=None,
        mode=None,
    ):
        self._key_type = key_type if key_type is not None else tf.int64
        self._handle_dtype = dtype if dtype is not None else tf.float32
        self._dimension = dimension
        self._indices = None
        self._mode = mode

        self._base = super(DynamicVariable, self)
        self._base.__init__(
            initial_value=[[0.0] * dimension],
            trainable=trainable,
            name="DynamicVariableBuffer",
            dtype=self._handle_dtype,
            constraint=constraint,
            distribute_strategy=None,
            synchronization=None,
            aggregation=None,
            shape=[None, dimension],
        )

        with ops.init_scope():
            name = "DynamicVariable" if name is None else name
            with ops.name_scope(name) as name_scope:
                self._dummy_name = ops.name_from_scope_name(name_scope)
                if context.executing_eagerly():
                    self._dummy_name = "%s_%d" % (name, ops.uid())
                with ops.NullContextmanager():
                    shape = [None, dimension]
                    initializer = "" if initializer is None else initializer
                    var_type = "dynamic" if var_type is None else var_type
                    handle = dynamic_variable_ops.dummy_var_handle(
                        container="DummyVariableContainer",
                        shared_name=self._dummy_name,
                        key_type=self._key_type,
                        dtype=self._handle_dtype,
                        shape=shape,
                    )
                    if type(initializer) is str:
                        init_op = dynamic_variable_ops.dummy_var_initialize(
                            handle,
                            initializer=initializer,
                            var_type=var_type,
                            unique_name=self._dummy_name,
                            key_type=self._key_type,
                            dtype=self._handle_dtype,
                        )
                    else:
                        with tf.control_dependencies([initializer._initializer_op]):
                            initial_val = initializer.read_value()
                        init_op = dynamic_variable_ops.dummy_var_initialize(
                            handle,
                            initializer=initial_val,
                            var_type=var_type,
                            unique_name=self._dummy_name,
                            key_type=self._key_type,
                            dtype=self._handle_dtype,
                        )
                    # TODO: Add is_initialized_op
                    # is_initialized_op = ops.convert_to_tensor(True)

                    self._tf_handle = self._handle
                    self._dummy_handle = handle
                    # Note that the default handle will be sok's handle
                    self._handle = self._dummy_handle
                    self._initializer_op = tf.group([self._initializer_op, init_op])
                    # self._is_initialized_op = tf.group([self._is_initialized_op, is_initialized_op])

            handle_data = (
                resource_variable_ops.cpp_shape_inference_pb2.CppShapeInferenceResult.HandleData()
            )
            handle_data.is_set = True
            handle_data.shape_and_type.append(
                resource_variable_ops.cpp_shape_inference_pb2.CppShapeInferenceResult.HandleShapeAndType(
                    shape=self.shape.as_proto(), dtype=self.dtype.as_datatype_enum
                )
            )
            resource_variable_ops._set_handle_shapes_and_types(
                self._handle, handle_data, graph_mode=False if context.executing_eagerly() else True
            )

    def is_static(self):
        return self._handle is self._tf_handle

    def to_static(self, indices):
        if not self.is_static() and self._indices is None:
            buffer = self.sparse_read(indices)
            self._indices = indices
            self._handle = self._tf_handle
            return self.assign(buffer)
        else:
            raise RuntimeError("to_static() must be called in dynamic mode.")

    def to_dynamic(self):
        if self.is_static():
            buffer = self.read_value()
            sparse_delta = ops.IndexedSlices(buffer, self._indices, self.shape)
            self._indices = None
            self._handle = self._dummy_handle
            return self.scatter_update(sparse_delta)
        else:
            raise RuntimeError("to_dynamic() must be called in static mode.")

    def __repr__(self):
        if self.is_static():
            return self._base.__repr__()
        return "<sok.DynamicVariable '%s' shape=%s dtype=%s>" % (
            self._dummy_name,
            self.shape,
            self.dtype.name,
        )

    @property
    def size(self):
        return dynamic_variable_ops.dummy_var_shape(
            self._dummy_handle, key_type=self._key_type, dtype=self._handle_dtype
        )

    @property
    def indices(self):
        return self._indices

    @property
    def dimension(self):
        return self._dimension

    @property
    def key_type(self):
        return self._key_type

    @property
    def handle_dtype(self):
        return self._handle_dtype

    @property
    def target_gpu(self):
        if self._mode is not None and self._mode[: len("localized")] == "localized":
            target_gpu = int(self._mode.split(":")[1])
            if target_gpu >= num_gpus():
                raise RuntimeError(
                    "There are only %d GPU(s), cannot put embedding table on %dth(zero-indexed) GPU."
                    % (num_gpus(), target_gpu)
                )
            return target_gpu
        return -1

    @property
    def num_gpus(self):
        return num_gpus()

    def key_map(self, indices):
        return indices

    # -------------------------------------------------------------------------
    # Methods supported both in static mode and dynamic mode
    # -------------------------------------------------------------------------

    def sparse_read(self, indices, name=None):
        if self.is_static():
            return self._base.sparse_read(indices, name)

        variable_accessed(self)
        if indices.dtype == tf.int32:
            indices = tf.cast(indices, tf.int64)
        return dynamic_variable_ops.dummy_var_sparse_read(
            self._dummy_handle, indices, dtype=self._handle_dtype
        )

    def scatter_sub(self, sparse_delta, use_locking=False, name=None):
        if self.is_static():
            return self._base.scatter_sub(sparse_delta, use_locking, name)
        if not isinstance(sparse_delta, ops.IndexedSlices):
            raise TypeError("sparse_delta is not IndexedSlices: %s" % sparse_delta)
        return dynamic_variable_ops.dummy_var_scatter_add(
            self._dummy_handle,
            sparse_delta.indices,
            ops.convert_to_tensor(-sparse_delta.values, self.dtype),
        )

    def scatter_add(self, sparse_delta, use_locking=False, name=None):
        if self.is_static():
            return self._base.scatter_add(sparse_delta, use_locking, name)
        if not isinstance(sparse_delta, ops.IndexedSlices):
            raise TypeError("sparse_delta is not IndexedSlices: %s" % sparse_delta)
        return dynamic_variable_ops.dummy_var_scatter_add(
            self._dummy_handle,
            sparse_delta.indices,
            ops.convert_to_tensor(sparse_delta.values, self.dtype),
        )

    def scatter_update(self, sparse_delta, use_locking=False, name=None):
        if self.is_static():
            return self._base.scatter_update(sparse_delta, use_locking, name)
        if not isinstance(sparse_delta, ops.IndexedSlices):
            raise TypeError("sparse_delta is not IndexedSlices: %s" % sparse_delta)
        return dynamic_variable_ops.dummy_var_scatter_update(
            self._dummy_handle,
            sparse_delta.indices,
            ops.convert_to_tensor(sparse_delta.values, self.dtype),
        )

    # -------------------------------------------------------------------------
    # Methods not supported both in static mode and dynamic mode
    # -------------------------------------------------------------------------

    def __deepcopy__(self, *args, **kwargs):
        raise NotImplementedError("__deepcopy__() is not supported.")

    def __reduce__(self, *args, **kwargs):
        raise NotImplementedError("__reduce__() is not supported.")

    def to_proto(self, *args, **kwargs):
        raise NotImplementedError("to_proto() is not supported.")

    @staticmethod
    def from_proto(*args, **kwargs):
        raise NotImplementedError("from_proto() is not supported.")

    def set_shape(self, *args, **kwargs):
        raise NotImplementedError("set_shape() is not supported.")

    # -------------------------------------------------------------------------
    # Methods only supported in static mode
    # -------------------------------------------------------------------------

    def is_initialized(self):
        if self.is_static():
            return self._base.is_initialized()
        raise NotImplementedError("is_initialized() is not supported in dynamic mode.")

    def _read_variable_op(self):
        if self.is_static():
            return self._base._read_variable_op()
        raise NotImplementedError("_read_variable_op() is not supported in dynamic mode.")

    def value(self):
        if self.is_static():
            return self._base.value()
        raise NotImplementedError("value() is not supported in dynamic mode.")

    def _dense_var_to_tensor(self, *args, **kwargs):
        if self.is_static():
            return self._base._dense_var_to_tensor(*args, **kwargs)
        raise NotImplementedError("_dense_var_to_tensor() is not supported in dynamic mode.")

    def _gather_saveables_for_checkpoint(self):
        if self.is_static():
            return self._base._gather_saveables_for_checkpoint()
        raise NotImplementedError(
            "_gather_saveables_for_checkpoint() is not supported in dynamic mode."
        )

    def gather_nd(self, *args, **kwargs):
        if self.is_static():
            return self._base.gather_nd(*args, **kwargs)
        raise NotImplementedError("gather_nd() is not supported in dynamic mode.")

    def assign_add(self, *args, **kwargs):
        if self.is_static():
            return self._base.assign_add(*args, **kwargs)
        raise NotImplementedError("assign_add() is not supported in dynamic mode.")

    def assign(self, *args, **kwargs):
        if self.is_static():
            return self._base.assign(*args, **kwargs)
        raise NotImplementedError("assign() is not supported in dynamic mode.")

    def scatter_max(self, *args, **kwargs):
        if self.is_static():
            return self._base.scatter_max(*args, **kwargs)
        raise NotImplementedError("scatter_max() is not supported in dynamic mode.")

    def scatter_min(self, *args, **kwargs):
        if self.is_static():
            return self._base.scatter_min(*args, **kwargs)
        raise NotImplementedError("scatter_min() is not supported in dynamic mode.")

    def scatter_mul(self, *args, **kwargs):
        if self.is_static():
            return self._base.scatter_mul(*args, **kwargs)
        raise NotImplementedError("scatter_mul() is not supported in dynamic mode.")

    def scatter_dim(self, *args, **kwargs):
        if self.is_static():
            return self._base.scatter_dim(*args, **kwargs)
        raise NotImplementedError("scatter_dim() is not supported in dynamic mode.")

    def batch_scatter_update(self, *args, **kwargs):
        if self.is_static():
            return self._base.batch_scatter_update(*args, **kwargs)
        raise NotImplementedError("batch_scatter_update() is not supported in dynamic mode.")

    def scatter_nd_sub(self, *args, **kwargs):
        if self.is_static():
            return self._base.scatter_nd_sub(*args, **kwargs)
        raise NotImplementedError("scatter_nd_sub() is not supported in dynamic mode.")

    def scatter_nd_update(self, *args, **kwargs):
        if self.is_static():
            return self._base.scatter_nd_update(*args, **kwargs)
        raise NotImplementedError("scatter_nd_update() is not supported in dynamic mode.")

    def _strided_slice_assign(self, *args, **kwargs):
        if self.is_static():
            return self._base._strided_slice_assign(*args, **kwargs)
        raise NotImplementedError("_strided_slice_assign() is not supported in dynamic mode.")

    def __int__(self, *args, **kwargs):
        if self.is_static():
            return self._base.__int__(*args, **kwargs)
        raise NotImplementedError("__int__() is not supported in dynamic mode.")


@tf.RegisterGradient("DummyVarSparseRead")
def _SparseReadGrad(op, grad):
    """Gradient for sparse_read."""
    handle = op.inputs[0]
    indices = op.inputs[1]
    key_type = op.get_attr("key_type")
    dtype = op.get_attr("dtype")
    variable_shape = dynamic_variable_ops.dummy_var_shape(handle, key_type=key_type, dtype=dtype)
    size = array_ops.expand_dims(array_ops.size(indices), 0)
    values_shape = array_ops.concat([size, variable_shape[1:]], 0)
    grad = array_ops.reshape(grad, values_shape)
    indices = array_ops.reshape(indices, size)
    return (ops.IndexedSlices(grad, indices, variable_shape), None)


def export(var):
    if isinstance(var, DynamicVariable):
        indices, values = dynamic_variable_ops.dummy_var_export(
            var.handle, key_type=var.key_type, dtype=var.handle_dtype
        )
        with tf.device("CPU"):
            indices = tf.identity(indices)
            values = tf.identity(values)
        return indices, values


def assign(var, indices, values):
    if isinstance(var, DynamicVariable):
        return dynamic_variable_ops.dummy_var_assign(var.handle, indices, values)
