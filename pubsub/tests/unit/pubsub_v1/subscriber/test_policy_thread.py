# Copyright 2017, Google LLC All rights reserved.
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

from __future__ import absolute_import

from concurrent import futures
import threading

from google.auth import credentials
import grpc
import mock
import pytest
from six.moves import queue

from google.cloud.pubsub_v1 import subscriber
from google.cloud.pubsub_v1 import types
from google.cloud.pubsub_v1.subscriber import _helper_threads
from google.cloud.pubsub_v1.subscriber import message
from google.cloud.pubsub_v1.subscriber.futures import Future
from google.cloud.pubsub_v1.subscriber.policy import thread


def create_policy(**kwargs):
    creds = mock.Mock(spec=credentials.Credentials)
    client = subscriber.Client(credentials=creds)
    return thread.Policy(client, 'sub_name_c', **kwargs)


def test_init():
    policy = create_policy()
    policy._callback(None)


def test_init_with_executor():
    executor = futures.ThreadPoolExecutor(max_workers=25)
    policy = create_policy(executor=executor, queue=queue.Queue())
    assert policy._executor is executor


def test_close():
    policy = create_policy()
    consumer = policy._consumer
    with mock.patch.object(consumer, 'stop_consuming') as stop_consuming:
        policy.close()
        stop_consuming.assert_called_once_with()
    assert 'callback request worker' not in policy._consumer.helper_threads


def test_close_with_future():
    policy = create_policy()
    policy._future = Future(policy=policy)
    consumer = policy._consumer
    with mock.patch.object(consumer, 'stop_consuming') as stop_consuming:
        future = policy.future
        policy.close()
        stop_consuming.assert_called_once_with()
    assert policy.future != future
    assert future.result() is None


@mock.patch.object(_helper_threads.HelperThreadRegistry, 'start')
@mock.patch.object(threading.Thread, 'start')
def test_open(thread_start, htr_start):
    policy = create_policy()
    with mock.patch.object(policy._consumer, 'start_consuming') as consuming:
        policy.open(mock.sentinel.CALLBACK)
        assert policy._callback is mock.sentinel.CALLBACK
        consuming.assert_called_once_with()
        htr_start.assert_called()
        thread_start.assert_called()


def test_on_callback_request():
    policy = create_policy()
    with mock.patch.object(policy, 'call_rpc') as call_rpc:
        policy.on_callback_request(('call_rpc', {'something': 42}))
        call_rpc.assert_called_once_with(something=42)


def test_on_exception_deadline_exceeded():
    policy = create_policy()
    exc = mock.Mock(spec=('code',))
    exc.code.return_value = grpc.StatusCode.DEADLINE_EXCEEDED
    assert policy.on_exception(exc) is None


def test_on_exception_other():
    policy = create_policy()
    policy._future = Future(policy=policy)
    exc = TypeError('wahhhhhh')
    with pytest.raises(TypeError):
        policy.on_exception(exc)
        policy.future.result()


def test_on_response():
    callback = mock.Mock(spec=())

    # Create mock ThreadPoolExecutor, pass into create_policy(), and verify
    # that both executor.submit() and future.add_done_callback are called
    # twice.
    future = mock.Mock()
    attrs = {'submit.return_value': future}
    executor = mock.Mock(**attrs)

    # Set up the policy.
    policy = create_policy(executor=executor)
    policy._callback = callback

    # Set up the messages to send.
    messages = (
        types.PubsubMessage(data=b'foo', message_id='1'),
        types.PubsubMessage(data=b'bar', message_id='2'),
    )

    # Set up a valid response.
    response = types.StreamingPullResponse(
        received_messages=[
            {'ack_id': 'fack', 'message': messages[0]},
            {'ack_id': 'back', 'message': messages[1]},
        ],
    )

    # Actually run the method and prove that executor.submit and
    # future.add_done_callback were called in the expected way.
    policy.on_response(response)

    submit_calls = [m for m in executor.method_calls if m[0] == 'submit']
    assert len(submit_calls) == 2
    for call in submit_calls:
        assert call[1][0] == callback
        assert isinstance(call[1][1], message.Message)

    add_done_callback_calls = [
        m for m in future.method_calls if m[0] == 'add_done_callback']
    assert len(add_done_callback_calls) == 2
    for call in add_done_callback_calls:
        assert call[1][0] == thread._callback_completed


def test__callback_completed():
    future = mock.Mock()
    thread._callback_completed(future)
    result_calls = [m for m in future.method_calls if m[0] == 'result']
    assert len(result_calls) == 1
