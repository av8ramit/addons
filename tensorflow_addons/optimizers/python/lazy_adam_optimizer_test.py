# Copyright 2019 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Tests for LazyAdamOptimizer."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np

from tensorflow.python.eager import context
from tensorflow.python.framework import constant_op
from tensorflow.python.framework import dtypes
from tensorflow.python.framework import ops
from tensorflow.python.framework import test_util
from tensorflow.python.ops import array_ops
from tensorflow.python.ops import math_ops
from tensorflow.python.ops import resource_variable_ops
from tensorflow.python.ops import variables
from tensorflow.python.platform import test
from tensorflow_addons.optimizers.python import lazy_adam_optimizer


def adam_update_numpy(param,
                      g_t,
                      t,
                      m,
                      v,
                      lr=0.001,
                      beta1=0.9,
                      beta2=0.999,
                      epsilon=1e-7):
    lr_t = lr * np.sqrt(1 - beta2**(t + 1)) / (1 - beta1**(t + 1))

    m_t = beta1 * m + (1 - beta1) * g_t
    v_t = beta2 * v + (1 - beta2) * g_t * g_t

    param_t = param - lr_t * m_t / (np.sqrt(v_t) + epsilon)
    return param_t, m_t, v_t


def get_beta_accumulators(opt, dtype):
    local_step = math_ops.cast(opt.iterations + 1, dtype)
    beta_1_t = math_ops.cast(opt._get_hyper("beta_1"), dtype)
    beta_1_power = math_ops.pow(beta_1_t, local_step)
    beta_2_t = math_ops.cast(opt._get_hyper("beta_2"), dtype)
    beta_2_power = math_ops.pow(beta_2_t, local_step)
    return (beta_1_power, beta_2_power)


class LazyAdamOptimizerTest(test.TestCase):

    # TODO: remove v1 tests (keep pace with adam_test.py in keras).
    @test_util.run_deprecated_v1
    def testSparse(self):
        for dtype in [dtypes.half, dtypes.float32, dtypes.float64]:
            with self.cached_session():
                # Initialize variables for numpy implementation.
                m0, v0, m1, v1 = 0.0, 0.0, 0.0, 0.0
                var0_np = np.array([1.0, 1.0, 2.0], dtype=dtype.as_numpy_dtype)
                grads0_np = np.array([0.1, 0.0, 0.1],
                                     dtype=dtype.as_numpy_dtype)
                var1_np = np.array([3.0, 3.0, 4.0], dtype=dtype.as_numpy_dtype)
                grads1_np = np.array([0.01, 0.0, 0.01],
                                     dtype=dtype.as_numpy_dtype)

                var0 = resource_variable_ops.ResourceVariable(var0_np)
                var1 = resource_variable_ops.ResourceVariable(var1_np)
                grads0_np_indices = np.array([0, 2], dtype=np.int32)
                grads0 = ops.IndexedSlices(
                    constant_op.constant(grads0_np[grads0_np_indices]),
                    constant_op.constant(grads0_np_indices),
                    constant_op.constant([3]))
                grads1_np_indices = np.array([0, 2], dtype=np.int32)
                grads1 = ops.IndexedSlices(
                    constant_op.constant(grads1_np[grads1_np_indices]),
                    constant_op.constant(grads1_np_indices),
                    constant_op.constant([3]))
                opt = lazy_adam_optimizer.LazyAdamOptimizer()
                update = opt.apply_gradients(
                    zip([grads0, grads1], [var0, var1]))
                self.evaluate(variables.global_variables_initializer())

                # Fetch params to validate initial values
                self.assertAllClose([1.0, 1.0, 2.0], self.evaluate(var0))
                self.assertAllClose([3.0, 3.0, 4.0], self.evaluate(var1))

                beta_1_power, beta_2_power = get_beta_accumulators(opt, dtype)
                # Run 3 steps of Adam
                for t in range(3):
                    self.assertAllCloseAccordingToType(
                        0.9**(t + 1), self.evaluate(beta_1_power))
                    self.assertAllCloseAccordingToType(
                        0.999**(t + 1), self.evaluate(beta_2_power))
                    self.evaluate(update)

                    var0_np, m0, v0 = adam_update_numpy(
                        var0_np, grads0_np, t, m0, v0)
                    var1_np, m1, v1 = adam_update_numpy(
                        var1_np, grads1_np, t, m1, v1)

                    # Validate updated params
                    self.assertAllCloseAccordingToType(var0_np,
                                                       self.evaluate(var0))
                    self.assertAllCloseAccordingToType(var1_np,
                                                       self.evaluate(var1))

    @test_util.run_deprecated_v1
    def testSparseDevicePlacement(self):
        for index_dtype in [dtypes.int32, dtypes.int64]:
            with self.cached_session(force_gpu=test.is_gpu_available()):
                # If a GPU is available, tests that all optimizer ops can be placed on
                # it (i.e. they have GPU kernels).
                var = variables.Variable([[1.0], [2.0]])
                indices = constant_op.constant([0, 1], dtype=index_dtype)
                g_sum = lambda: math_ops.reduce_sum(array_ops.gather(var, indices))  # pylint: disable=cell-var-from-loop
                optimizer = lazy_adam_optimizer.LazyAdamOptimizer(3.0)
                minimize_op = optimizer.minimize(g_sum, var_list=[var])
                self.evaluate(variables.global_variables_initializer())
                self.evaluate(minimize_op)

    @test_util.run_deprecated_v1
    def testSparseRepeatedIndices(self):
        for dtype in [dtypes.half, dtypes.float32, dtypes.float64]:
            with self.cached_session():
                repeated_index_update_var = variables.Variable([[1.0], [2.0]],
                                                               dtype=dtype)
                aggregated_update_var = variables.Variable([[1.0], [2.0]],
                                                           dtype=dtype)
                grad_repeated_index = ops.IndexedSlices(
                    constant_op.constant([0.1, 0.1],
                                         shape=[2, 1],
                                         dtype=dtype),
                    constant_op.constant([1, 1]),
                    constant_op.constant([2, 1]))
                grad_aggregated = ops.IndexedSlices(
                    constant_op.constant([0.2], shape=[1, 1], dtype=dtype),
                    constant_op.constant([1]), constant_op.constant([2, 1]))
                repeated_update_opt = lazy_adam_optimizer.LazyAdamOptimizer()
                repeated_update = repeated_update_opt.apply_gradients(
                    [(grad_repeated_index, repeated_index_update_var)])
                aggregated_update_opt = lazy_adam_optimizer.LazyAdamOptimizer()
                aggregated_update = aggregated_update_opt.apply_gradients(
                    [(grad_aggregated, aggregated_update_var)])
                self.evaluate(variables.global_variables_initializer())
                self.assertAllClose(aggregated_update_var.eval(),
                                    repeated_index_update_var.eval())
                for _ in range(3):
                    repeated_update.run()
                    aggregated_update.run()
                    self.assertAllClose(aggregated_update_var.eval(),
                                        repeated_index_update_var.eval())

    def doTestBasic(self, use_callable_params=False):
        for i, dtype in enumerate(
            [dtypes.half, dtypes.float32, dtypes.float64]):
            with self.session(graph=ops.Graph()):
                # Initialize variables for numpy implementation.
                m0, v0, m1, v1 = 0.0, 0.0, 0.0, 0.0
                var0_np = np.array([1.0, 2.0], dtype=dtype.as_numpy_dtype)
                grads0_np = np.array([0.1, 0.1], dtype=dtype.as_numpy_dtype)
                var1_np = np.array([3.0, 4.0], dtype=dtype.as_numpy_dtype)
                grads1_np = np.array([0.01, 0.01], dtype=dtype.as_numpy_dtype)

                var0 = resource_variable_ops.ResourceVariable(
                    var0_np, name="var0_%d" % i)
                var1 = resource_variable_ops.ResourceVariable(
                    var1_np, name="var1_%d" % i)
                grads0 = constant_op.constant(grads0_np)
                grads1 = constant_op.constant(grads1_np)

                learning_rate = lambda: 0.001
                beta1 = lambda: 0.9
                beta2 = lambda: 0.999
                epsilon = lambda: 1e-8
                if not use_callable_params:
                    learning_rate = learning_rate()
                    beta1 = beta1()
                    beta2 = beta2()
                    epsilon = epsilon()

                opt = lazy_adam_optimizer.LazyAdamOptimizer(
                    learning_rate=learning_rate)
                if not context.executing_eagerly():
                    update = opt.apply_gradients(
                        zip([grads0, grads1], [var0, var1]))
                    self.evaluate(variables.global_variables_initializer())
                    # Fetch params to validate initial values
                    self.assertAllClose([1.0, 2.0], self.evaluate(var0))
                    self.assertAllClose([3.0, 4.0], self.evaluate(var1))

                # Run 3 steps of Adam
                for t in range(3):
                    beta_1_power, beta_2_power = get_beta_accumulators(
                        opt, dtype)
                    self.assertAllCloseAccordingToType(
                        0.9**(t + 1), self.evaluate(beta_1_power))
                    self.assertAllCloseAccordingToType(
                        0.999**(t + 1), self.evaluate(beta_2_power))
                    if not context.executing_eagerly():
                        self.evaluate(update)
                    else:
                        opt.apply_gradients(
                            zip([grads0, grads1], [var0, var1]))

                    var0_np, m0, v0 = adam_update_numpy(
                        var0_np, grads0_np, t, m0, v0)
                    var1_np, m1, v1 = adam_update_numpy(
                        var1_np, grads1_np, t, m1, v1)

                    # Validate updated params
                    self.assertAllCloseAccordingToType(var0_np,
                                                       self.evaluate(var0))
                    self.assertAllCloseAccordingToType(var1_np,
                                                       self.evaluate(var1))
                    self.assertEqual("var0_%d/m:0" % (i, ),
                                     opt.get_slot(var0, "m").name)

    @test_util.run_in_graph_and_eager_modes(reset_test=True)
    def testResourceBasic(self):
        self.doTestBasic()

    def testBasicCallableParams(self):
        with context.eager_mode():
            self.doTestBasic(use_callable_params=True)

    @test_util.run_deprecated_v1
    def testTensorLearningRate(self):
        for dtype in [dtypes.half, dtypes.float32, dtypes.float64]:
            with self.cached_session():
                # Initialize variables for numpy implementation.
                m0, v0, m1, v1 = 0.0, 0.0, 0.0, 0.0
                var0_np = np.array([1.0, 2.0], dtype=dtype.as_numpy_dtype)
                grads0_np = np.array([0.1, 0.1], dtype=dtype.as_numpy_dtype)
                var1_np = np.array([3.0, 4.0], dtype=dtype.as_numpy_dtype)
                grads1_np = np.array([0.01, 0.01], dtype=dtype.as_numpy_dtype)

                var0 = variables.Variable(var0_np)
                var1 = variables.Variable(var1_np)
                grads0 = constant_op.constant(grads0_np)
                grads1 = constant_op.constant(grads1_np)
                opt = lazy_adam_optimizer.LazyAdamOptimizer(
                    constant_op.constant(0.001))
                update = opt.apply_gradients(
                    zip([grads0, grads1], [var0, var1]))
                self.evaluate(variables.global_variables_initializer())

                # Fetch params to validate initial values
                self.assertAllClose([1.0, 2.0], var0.eval())
                self.assertAllClose([3.0, 4.0], var1.eval())

                beta_1_power, beta_2_power = get_beta_accumulators(opt, dtype)
                # Run 3 steps of Adam
                for t in range(3):
                    self.assertAllCloseAccordingToType(
                        0.9**(t + 1), self.evaluate(beta_1_power))
                    self.assertAllCloseAccordingToType(
                        0.999**(t + 1), self.evaluate(beta_2_power))
                    self.evaluate(update)

                    var0_np, m0, v0 = adam_update_numpy(
                        var0_np, grads0_np, t, m0, v0)
                    var1_np, m1, v1 = adam_update_numpy(
                        var1_np, grads1_np, t, m1, v1)

                    # Validate updated params
                    self.assertAllCloseAccordingToType(var0_np,
                                                       self.evaluate(var0))
                    self.assertAllCloseAccordingToType(var1_np,
                                                       self.evaluate(var1))

    @test_util.run_deprecated_v1
    def testSharing(self):
        for dtype in [dtypes.half, dtypes.float32, dtypes.float64]:
            with self.cached_session():
                # Initialize variables for numpy implementation.
                m0, v0, m1, v1 = 0.0, 0.0, 0.0, 0.0
                var0_np = np.array([1.0, 2.0], dtype=dtype.as_numpy_dtype)
                grads0_np = np.array([0.1, 0.1], dtype=dtype.as_numpy_dtype)
                var1_np = np.array([3.0, 4.0], dtype=dtype.as_numpy_dtype)
                grads1_np = np.array([0.01, 0.01], dtype=dtype.as_numpy_dtype)

                var0 = variables.Variable(var0_np)
                var1 = variables.Variable(var1_np)
                grads0 = constant_op.constant(grads0_np)
                grads1 = constant_op.constant(grads1_np)
                opt = lazy_adam_optimizer.LazyAdamOptimizer()
                update1 = opt.apply_gradients(
                    zip([grads0, grads1], [var0, var1]))
                update2 = opt.apply_gradients(
                    zip([grads0, grads1], [var0, var1]))
                self.evaluate(variables.global_variables_initializer())

                beta_1_power, beta_2_power = get_beta_accumulators(opt, dtype)

                # Fetch params to validate initial values
                self.assertAllClose([1.0, 2.0], self.evaluate(var0))
                self.assertAllClose([3.0, 4.0], self.evaluate(var1))

                # Run 3 steps of intertwined Adam1 and Adam2.
                for t in range(3):
                    self.assertAllCloseAccordingToType(
                        0.9**(t + 1), self.evaluate(beta_1_power))
                    self.assertAllCloseAccordingToType(
                        0.999**(t + 1), self.evaluate(beta_2_power))
                    if t % 2 == 0:
                        update1.run()
                    else:
                        update2.run()

                    var0_np, m0, v0 = adam_update_numpy(
                        var0_np, grads0_np, t, m0, v0)
                    var1_np, m1, v1 = adam_update_numpy(
                        var1_np, grads1_np, t, m1, v1)

                    # Validate updated params
                    self.assertAllCloseAccordingToType(var0_np,
                                                       self.evaluate(var0))
                    self.assertAllCloseAccordingToType(var1_np,
                                                       self.evaluate(var1))

    def testSlotsUniqueEager(self):
        with context.eager_mode():
            v1 = resource_variable_ops.ResourceVariable(1.)
            v2 = resource_variable_ops.ResourceVariable(1.)
            opt = lazy_adam_optimizer.LazyAdamOptimizer(1.)
            opt.minimize(lambda: v1 + v2, var_list=[v1, v2])
            # There should be iteration, and two unique slot variables for v1 and v2.
            self.assertEqual(5, len(set(opt.variables())))
            self.assertEqual(
                self.evaluate(opt.variables()[0]),
                self.evaluate(opt.iterations))


if __name__ == "__main__":
    test.main()
