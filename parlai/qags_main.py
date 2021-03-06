#!/usr/bin/env python3

# Copyright (c) Facebook, Inc. and its affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
import os
import json
import time
import ipdb

from queue import Queue
import numpy as np

from parlai.core.params import ParlaiParser
from parlai.mturk.core.mturk_manager import StaticMTurkManager
from parlai.mturk.core.worlds import StaticMTurkTaskWorld
#from parlai_internal.mturk.tasks.pairwise_dialogue_eval.task_config\
#    import task_config
import parlai.mturk.core.mturk_utils as mturk_utils


display_agent_name = 'RatingWorker'

task_queue = Queue()

onboarding_tasks = {}
onboarding_conv_ids = []
ONBOARD_FAIL_MSG = 'Failed quality control check'
SHORT_RESPONSE_MSG = 'Provided reason is too short'
onboarding_failed_workers = []

desired_tasks = {}
conversations_to_tasks = {}

workers_to_desired_tasks_completed = {}
workers_to_onboarding_tasks_todo = {}



def make_flags(from_argv=False):
    """ Add arguments to parser and either parse from commandline or initialize
    to defaults (for overriding in scripts)
    """
    argparser = ParlaiParser(False, False)
    argparser.add_parlai_data_path()
    argparser.add_mturk_args()
    argparser.add_argument(
     '--dialogs_path', type=str, default=None,
     help='path to folder with conversation log files for evaluation'
    )
    argparser.add_argument(
     '--out_file', type=str, default=None,
     help='path to file to write worker data'
    )
    argparser.add_argument(
     '--bad_worker_file', type=str, default=None,
     help='(optional) path to file with workers to exclude'
    )
    argparser.add_argument(
     '--annotations_per_pair', type=int, default=1,
     help='Number of annotations per conversation comparison pair'
    )
    argparser.add_argument(
     '--pair_data', type=list, default=None,
     help='list of (conv1, conv2, hit, desc) (for pre-chosen pairs, e.g. for replicating previous experiments)'
    )
    argparser.add_argument(
     '--onboard_pair_data', type=list, default=None,
     help='list of (conv1, conv2, hit, desc) onboarding tasks (for pre-chosen pairs, e.g. for replicating previous experiments)'
    )
    argparser.add_argument(
     '--s1_choice', type=str, default='',
     help='text next to speaker 1 radio button'
    )
    argparser.add_argument(
     '--s2_choice', type=str, default='',
     help='text next to speaker 2 radio button'
    )
    argparser.add_argument(
     '--mode', type=str, choices=['precision', 'recall'], default='precision',
     help='HIT task type'
    )
    argparser.add_argument(
     '--question', type=str, default='Is the sentence supported by the article?',
     help='question to present to turker for comparison (e.g. "Which speaker is better?")'
    )
    argparser.add_argument(
     '--correctness_is_flipped', default=False, action='store_true',
     help='question phrasing flips the better model - e.g. question is "Which speaker is more boring?"'
    )
    argparser.add_argument(
     '--model_comparisons', type=str,
     help='list of model pairs to compare, comma separated. E.g. ["transformer,human_eval"] '
    )
    argparser.add_argument(
     '--pairs_per_matchup', type=int, default=160,
     help='Number of conversation pairs to generate for the comparison'
    )
    argparser.add_argument(
     '--num_onboarding_tasks', type=int, default=5,
     help='Number of onboarding tasks total to screen workers with'
    )
    argparser.add_argument(
     '--block_on_onboarding', action='store_false',
     help='whether to block on onboarding failure'
    )
    argparser.add_argument(
     '--block_qualification', type=str, default='testytestytest',
     help='unique name of block for this job'
    )
    argparser.add_argument(
     '--onboarding_tasks', type=list, default=None,
     help='onboarding tasks to screen workers with, list of (conv1id, conv2id, matchup) tuples'
    )
    argparser.add_argument(
     '--onboarding_model_comparison', type=str, default='greedy_model,human_eval',
     help='models to compare for the onboarding task. E.g. "greedy,human_eval" '
    )
    argparser.add_argument(
     '--comparisons_per_hit', type=int, default=5,
     help='number of comparisons to do per hit'
    )
    argparser.add_argument(
     '--onboarding_threshold', type=float, default=.75,
     help='minimum accuracy on onboarding tasks, as a float 0-1.0'
    )
    argparser.add_argument(
     '--seed', type=int, default=42,
     help='np.random seed'
    )
    argparser.set_defaults(allowed_conversation=1)
    if from_argv:
        return argparser.parse_args()
    else:
        return argparser.parse_args(args=[])


def list_files(folder):
    for fn in os.listdir(folder):
        full_fn = os.path.join(folder, fn)
        if os.path.isfile(full_fn):
            yield full_fn
        elif os.path.isdir(full_fn):
            for sfn in list_files(full_fn):
                yield sfn


def setup_task_queue(opt):
    """ Initialize task queue to contain the specified number of instances of
    each pairing
    """
    # hacky fix for the parlai parser hacky fix
    data_folder = opt['dialogs_path'].replace('-', '_')
    annotations_per_pair = opt['annotations_per_pair']
    all_conv_data = {}
    conv_ids_by_model = {}
    internal_id = 0

    # read in all conversation data
    print(f"Loading data from {data_folder}")
    for data_fn in os.listdir(data_folder):
        if data_fn.endswith("swp"):
            continue
        full_data_fn = os.path.join(data_folder, data_fn)
        print(f"Loading data from {full_data_fn}")
        with open(full_data_fn, 'r', encoding='utf-8') as dialog_data_file:
            for l in dialog_data_file:
                try:
                    single_task_json = json.loads(l)
                except:
                    print(f"Failed to load a line from {data_fn}")
                    print(f"Bad line: {l}")
                id = single_task_json.get('ex_idx')
                #id = single_task_json.get('assignment_id_hashed')
                if id is None:
                    id = single_task_json['pair_id']
                if isinstance(id, list):
                    id = tuple(id)
                # model_name = single_task_json['model_name']
                model_name = data_fn.split('.')[0]
                all_conv_data[id] = single_task_json
                model_convs = conv_ids_by_model.get(model_name)
                if model_convs is None:
                    model_convs = []
                    conv_ids_by_model[model_name] = model_convs
                model_convs.append(id)

    #####
    ## Set up onboarding tasks
    #####
    if opt['onboarding_tasks']:
        for (id1, id2, matchup) in opt['onboarding_tasks']:
            task = make_task_from_ids(
                id1, id2, internal_id, all_conv_data, opt['s1_choice'], opt['s2_choice'],
                opt['question'], opt['correctness_is_flipped'], matchup=matchup, mode=opt['mode']
            )
            conv1 = all_conv_data.get(id1)
            conv2 = all_conv_data.get(id2)
            onboarding_conv_ids.extend([conv1, conv2])
            onboarding_tasks[internal_id] = task

            internal_id += 1
    else:
        # make random onboarding tasks
        raise NotImplementedError("Provide hand-picked onboarding task for now")
        num_onboarding_tasks = opt['num_onboarding_tasks']
        num_qual1s = int(num_onboarding_tasks / 2)
        num_qual2s = num_onboarding_tasks - num_qual1s
        onboarding_model_comparison = opt['onboarding_model_comparison'].split(',')
        if len(conv_ids_by_model[onboarding_model]) < num_onboarding_tasks * 3:
            raise ValueError("Not enough model conversations for # onboarding tasks")
        onboarding_model_convs = np.random.choice(
            conv_ids_by_model[onboarding_model], num_qual1s + 2*num_qual2s, False
        )
        onboarding_human_convs = np.random.choice(
            conv_ids_by_model['human_eval'], num_qual1s, False
        )
        for i in range(num_qual1s):
            id1 = onboarding_model_convs[i]
            id2 = onboarding_human_convs[i]
            make_task_from_ids(
                id1, id2, internal_id, all_conv_data, opt['s1_choice'], opt['s2_choice'],
                opt['question'], opt['correctness_is_flipped'], matchup='qual1', mode=opt['mode']
            )
            internal_id += 1

    #####
    ## Create main tasks
    #####
    if opt['pair_data']:  # replicating a previous run
        print('{} distinct hits found. Example:'.format(len(opt['pair_data'])))
        for (id1, id2, hit_id, matchup) in opt['pair_data']:
            task = make_task_from_ids(
                id1, id2, internal_id, all_conv_data, opt['s1_choice'], opt['s2_choice'],
                opt['question'], opt['correctness_is_flipped'], hit_id, matchup, mode=opt['mode']
            )
            desired_tasks[internal_id] = task
            for id in [id1, id2]:
                if id not in conversations_to_tasks:
                    conversations_to_tasks[id] = []
                conversation_task_list = conversations_to_tasks[id]
                conversation_task_list.append(id)
            internal_id += 1

    elif opt['model_comparisons']:
        n_pairs = opt['pairs_per_matchup']
        for model_0, model_1 in opt['model_comparisons']:
            assert (model_0 in conv_ids_by_model and model_1 in conv_ids_by_model), \
                    print(f"Found {conv_ids_by_model.keys()}\nPlease provide a list of tuples of valid models in --model_comparison")

            matchup_name = '{},{}'.format(model_0, model_1)
            conv_pairs = []
            all_model1_convs = [
                id for id in conv_ids_by_model[model_0] if id not in onboarding_conv_ids
            ]
            all_model2_convs = [
                id for id in conv_ids_by_model[model_1] if id not in onboarding_conv_ids
            ]

            all_par_idxs = list(set([i[1] for i in all_model1_convs]))
            tmp = list(set([i[1] for i in all_model2_convs]))
            assert all_par_idxs == tmp

            for par_idx in all_par_idxs[:n_pairs]:

                pars = [id for id in conv_ids_by_model[model_0] if id[1] == par_idx]
                assert len(pars) == 1, print(f"Found too many items for {par_idx}")
                par_id = pars[0]

                sent_ids = [id for id in conv_ids_by_model[model_1] if id[1] == par_idx]
                sent_ids.sort(key=lambda x: x[2])

                par_tasks = []
                for sent_id in sent_ids:
                    if (par_id, sent_id) in conv_pairs:
                        continue
                    conv_pairs.append((par_id, sent_id))

                    task = make_task_from_ids(
                        par_id, sent_id, internal_id, all_conv_data, opt['s1_choice'], opt['s2_choice'],
                        opt['question'], opt['correctness_is_flipped'], matchup=matchup_name, mode=opt['mode']
                    )
                    par_tasks.append(task)
                    for id in [par_id, sent_id]:
                        if id not in conversations_to_tasks:
                            conversations_to_tasks[id] = []
                        conversation_task_list = conversations_to_tasks[id]
                        conversation_task_list.append(id)
                    internal_id += 1

                desired_tasks[par_id] = par_tasks

    # make desired tasks randomly from scratch
    elif opt['random_pairing']:
        for model_0, model_1 in opt['model_comparisons']:
            if (model_0 not in conv_ids_by_model or model_1 not in conv_ids_by_model):
                print(conv_ids_by_model.keys())
                raise ValueError("Please provide a list of tuples of valid models in --model_comparison")
            num_pairs = opt['pairs_per_matchup']
            matchup_name = '{},{}'.format(model_0, model_1)
            conv_pairs = []
            all_model1_convs = [
                id for id in conv_ids_by_model[model_0] if id not in onboarding_conv_ids
            ]
            all_model2_convs = [
                id for id in conv_ids_by_model[model_1] if id not in onboarding_conv_ids
            ]
            while len(conv_pairs) < num_pairs:
                id1 = np.random.choice(all_model1_convs)
                id2 = np.random.choice(all_model2_convs)
                if (id1, id2) in conv_pairs:
                    continue
                conv_pairs.append((id1, id2))

                task = make_task_from_ids(
                    id1, id2, internal_id, all_conv_data, opt['s1_choice'], opt['s2_choice'],
                    opt['question'], opt['correctness_is_flipped'], matchup=matchup_name, mode=opt['mode']
                )
                desired_tasks[internal_id] = task
                for id in [id1, id2]:
                    if id not in conversations_to_tasks:
                        conversations_to_tasks[id] = []
                    conversation_task_list = conversations_to_tasks[id]
                    conversation_task_list.append(id)
                internal_id += 1
    else:
        raise NotImplementedError("Provide --pair_data or --model_comparison")

    #####
    ## Fill task queue
    #####
    for i in range(annotations_per_pair):
        all_task_keys = list(desired_tasks.keys())
        np.random.shuffle(all_task_keys)
        for internal_id in all_task_keys:
            task_queue.put(desired_tasks[internal_id])
    if opt['max_hits_per_worker'] == 0:
        opt['max_hits_per_worker'] = (
            (len(desired_tasks) + len(onboarding_tasks)) / opt['comparisons_per_hit'])
    print(opt)


def make_task_from_ids(
    id1, id2, internal_id, all_conv_data, s1_choice, s2_choice, question,
    is_flipped, hitid='', matchup='regular', mode='precision',
):
    """ Create task_data dictionary and return it """
    conv_orders = [[0, 1], [1, 0]]
    conv1 = all_conv_data.get(id1)
    conv2 = all_conv_data.get(id2)
    if conv1 is None or conv2 is None:
        raise Exception("One of assignment ids {}, {} not found".format(
            id1, id2
        ))
    task_data = {}
    task_data['conversations'] = [conv1, conv2]
    specs = {}
    task_data['task_specs'] = specs
    specs['comparison_type'] = matchup
    specs['original_hit_id'] = hitid
    #specs['conversation_order'] = conv_orders[np.random.choice([0, 1])]
    specs['conversation_order'] = conv_orders[0]
    specs['internal_id'] = internal_id
    specs['s1_choice'] = s1_choice
    specs['s2_choice'] = s2_choice
    specs['question'] = question
    specs['correctness_is_flipped'] = is_flipped
    specs['speakers_to_eval'] = ['model', 'model']
    specs['mode'] = mode
    if matchup.startswith('qual'):
        specs['is_onboarding'] = True
        specs['answer'] = conv2["answer"] if "answer" in conv2 else None

    return task_data


def get_new_task_data(worker, tasks_per_hit):
    """ Get next task for worker. Returns the next onboarding task if worker
    hasn't finished them all, or finds a task from the queue they haven't done
    If they've seen everything in the queue, spin up an extra task (one that
    was in the queue and is now saturated)
    """
    worker_id = worker.worker_id
    task_data = get_onboarding_tasks(worker_id, tasks_per_hit)
    if len(task_data) == tasks_per_hit:
        return task_data
    tries = 0
    completed_tasks = workers_to_desired_tasks_completed.get(worker_id, [])
    while (not task_queue.empty()) and tries < task_queue.qsize():
        try:
            next_tasks = task_queue.get()
        except Queue.Empty:
            break
        tries += 1

        task_ids = [t['task_specs']['internal_id'] for t in next_tasks]
        if (not any([i in completed_tasks for i in task_ids])):
            # if the task has not been
            #   1) completed by the worker
            # update their info so that they've completed this task
            completed_tasks.extend(task_ids)
            workers_to_desired_tasks_completed[worker_id] = completed_tasks
            #task_data.append(next_task)
            #if len(task_data) == tasks_per_hit:
            task_data.extend(next_tasks) # next_task is a list of tasks already
            return task_data
        else:
            task_queue.put(next_tasks)

    # task queue is exhausted
    #tasks_still_needed = tasks_per_hit - len(task_data)
    #tasks_remaining = [id for id in desired_tasks.keys() if id not in completed_tasks]
    #tasks_chosen = [
    #    t for t in tasks_remaining
    #    if desired_tasks[t]['conversations'][0] not in seen_conversations
    #    and desired_tasks[t]['conversations'][1] not in seen_conversations
    #]
    #if tasks_still_needed < len(tasks_chosen):
    #    tasks_chosen = np.random.choice(tasks_chosen, tasks_still_needed, replace=False)
    #completed_tasks.extend(tasks_chosen)
    #seen_conversations.extend([desired_tasks[t]['conversations'][0] for t in tasks_chosen])
    #seen_conversations.extend([desired_tasks[t]['conversations'][1] for t in tasks_chosen])
    #task_data.extend([desired_tasks[id] for id in tasks_chosen])

    print(task_data)
    return task_data


def return_task_data(worker_id, task_data):
    """ When worker doesn't complete a task, return it to the queue or
    change their onboarding status depending on the task"""
    is_onboarding = False

    # un-list the task as one the worker has completed
    for subtask_data in task_data:
        if subtask_data['task_specs'].get('is_onboarding', False):
            workers_to_onboarding_tasks_todo[worker_id].append(
                subtask_data['task_specs']['internal_id'])
            is_onboarding = True
        else:
            workers_to_desired_tasks_completed[worker_id].remove(
                subtask_data['task_specs']['internal_id'])

    # put the HIT back on the task queue
    if not is_onboarding:
        task_queue.put(task_data)


def save_data(data, file_handle):
    """ Write data to open output file handle
    """
    file_handle.write(f"{data}\n")
    return


def get_onboarding_tasks(worker_id, tasks_per_hit):
    """ Get the next onboarding task for this worker id. If the worker has never
    done a task, shuffle the onboarding tasks for them. If they've done all
    of the onboarding tasks or if there are no onboarding tasks, return None
    """

    # no onboarding tasks
    if len(onboarding_tasks) == 0:
        return []

    onboarding_tasks_todo = workers_to_onboarding_tasks_todo.get(worker_id)

    # new worker
    if onboarding_tasks_todo is None:
        onboarding_tasks_todo = list(onboarding_tasks.keys())
        np.random.shuffle(onboarding_tasks_todo)
        workers_to_onboarding_tasks_todo[worker_id] = onboarding_tasks_todo

    # worker has completed all onboarding tasks
    if len(onboarding_tasks_todo) == 0:
        return []

    assert len(onboarding_tasks_todo) == len(onboarding_tasks), \
            "Invalid number of onboarding tasks found!"

    # just return all onboarding tasks
    #num_tasks_to_return = min(len(onboarding_tasks_todo), tasks_per_hit)
    #onboarding_tasks_chosen = onboarding_tasks_todo[:num_tasks_to_return]
    #workers_to_onboarding_tasks_todo[worker_id] = onboarding_tasks_todo[num_tasks_to_return:]
    #return [onboarding_tasks[id] for id in onboarding_tasks_chosen]
    return [onboarding_tasks[id] for id in onboarding_tasks_todo]


def check_and_update_worker_approval(mturk_manager, worker_id, threshold, save_data,
        bad_worker_fh=None):
    """ Reject and soft block workers who fail onboarding tasks """

    worker_data = save_data['worker_data'][worker_id]
    task_data = worker_data['task_data']
    responses = worker_data['response']
    num_onboarding_tasks = 0
    num_correct = 0

    short_msg_flag = 0
    short_time_flag = 0 # currently not used

    # check the tasks
    for i, task_datum in enumerate(task_data):
        task_specs = task_datum['task_specs']
        response = responses['task_data'][i]
        text_response = response['textReason']
        choice_response = float(response['speakerChoice'])

        # one or more msg was too short
        if (not text_response) or (len(text_response) < 3):
            short_msg_flag = 1

        if not task_specs.get('is_onboarding', False):
            continue

        # extract answer
        if task_specs['answer'] is not None:
            expected_response = 1 if task_specs['answer'] == 'yes' else 2
        else:
            expected_response = (
                1 if ((task_specs['conversation_order'] == [1, 0] and not task_specs['correctness_is_flipped']) or
                (task_specs['conversation_order'] == [0, 1] and task_specs['correctness_is_flipped']))
                else 2)

        # bookkeeping
        num_onboarding_tasks += 1
        num_correct += int(choice_response == expected_response)

    # review for non-onboarding tasks
    if num_onboarding_tasks == 0:

        # auto-fail workers who didn't pass onboarding
        if worker_id in onboarding_failed_workers:
            mturk_manager.reject_work(worker_data['assignment_id'], ONBOARD_FAIL_MSG)
            return_task_data(worker_id, task_data)

        # fail workers who msgs were too short
        if short_msg_flag:
            mturk_manager.reject_work(worker_data['assignment_id'], SHORT_RESPONSE_MSG)
            return_task_data(worker_id, task_data)

        return

    # onboarding tasks
    if (num_correct / num_onboarding_tasks) >= threshold and not short_msg_flag:
        # Passed quality control, continue
        return
    else:
        # Failed quality control
        msg = SHORT_RESPONSE_MSG if short_msg_flag else ONBOARD_FAIL_MSG
        mturk_manager.reject_work(worker_data['assignment_id'], msg)
        mturk_manager.soft_block_worker(worker_id)
        onboarding_failed_workers.append(worker_id)
        if bad_worker_fh is not None:
            bad_worker_fh.write(f"{worker_id}\n")
        print(f"\tSoft blocking worker {worker_id}")

    return


def main(opt, task_config):
    """Handles setting up and running a ParlAI-MTurk task by instantiating
    an MTurk manager and configuring it for the qa_data_collection task
    """

    np.random.seed(opt['seed'])

    # Set the task name to be the folder name
    opt['task'] = os.path.basename(os.path.dirname(os.path.abspath(__file__)))

    # append the contents of task_config.py to the configuration
    opt.update(task_config)

    # set up the HITs, which I think doesn't require a server
    setup_task_queue(opt)

    # Instantiate an MTurkManager with the given options and a maximum number
    # of agents per world of 1 (based on the length of mturk_agent_ids)
    mturk_manager = StaticMTurkManager(opt=opt)

    # Set up Heroku server
    mturk_manager.setup_server(
        task_directory_path=os.path.dirname(os.path.abspath(__file__)))

    # No onboarding function supported for static worlds at the moment,
    # should filter by making the first task against a "gold" example
    # which is processed in run_conversation at the moment.
    # Soon will support this behavior automatically
    mturk_manager.set_onboard_function(onboard_function=None)

    if opt['block_on_onboarding'] and opt['block_qualification'] is None:
        raise Exception("You must set block_qualification or set block_on_onboarding to False")

    out_fh = open(opt['out_file'], 'w')
    if opt['bad_worker_file'] is not None:
        with open(opt['bad_worker_file'], 'r') as bad_worker_fh:
            workers_to_block = [worker.strip() for worker in bad_worker_fh]
        bad_worker_fh = open(opt['bad_worker_file'], 'a')
    else:
        workers_to_block = []
        bad_worker_fh = None

    try:
        # Initialize run information
        mturk_manager.start_new_run()

        # Set up the sockets and threads to recieve workers
        mturk_manager.ready_to_accept_workers()

        # Create the hits as specified by command line arguments
        mturk_manager.create_hits()

        if opt['bad_worker_file'] is not None:
            for worker_id in workers_to_block:
                mturk_manager.soft_block_worker(worker_id)

        def check_worker_eligibility(worker):
            return True

        def assign_worker_roles(workers):
            workers[0].id = display_agent_name

        # This function may be automatically implemented by StaticMTurkManager
        # soon, in which case you just need to provide get_new_task_data() and
        # return_task_data()
        def run_conversation(mturk_manager, opt, workers):
            task_data = get_new_task_data(workers[0], opt['comparisons_per_hit'])

            world = StaticMTurkTaskWorld(
                opt,
                mturk_agent=workers[0],
                task_data=task_data,
            )
            while not world.episode_done():
                world.parley()
            print("Finished running task.")

            world.shutdown()
            print("Finished shutting down.")

            to_save_data = world.prep_save_data(workers)
            print("Finished preparing save data.")

            if not world.did_complete():
                print("Didn't finish HIT. Returning task data...")
                return_task_data(workers[0].worker_id, task_data)
            elif opt['block_on_onboarding']:
                print("Reviewing onboarding...")
                check_and_update_worker_approval(
                    mturk_manager, workers[0].worker_id, opt['onboarding_threshold'], to_save_data
                )
                print("\tDone reviewing onboarding")

            save_data(to_save_data, out_fh)
            return to_save_data

        print("This run id: {}".format(mturk_manager.task_group_id))

        # Begin the task, allowing mturk_manager to start running the task
        # world on any workers who connect
        mturk_manager.start_task(
            eligibility_function=check_worker_eligibility,
            assign_role_function=assign_worker_roles,
            task_function=run_conversation
        )

    except BaseException:
        raise

    finally:
        # Any hits that aren't claimed or completed have to be shut down. Must
        # keep the world running until that point.
        mturk_manager.expire_all_unassigned_hits()

        # Close file handles
        out_fh.close()
        if opt['bad_worker_file'] is not None:
            bad_worker_fh.close()

        # Shutdown the manager and free all related resources
        mturk_manager.shutdown()

        print(f"ALL ONBOARDING FAILED WORKERS: {onboarding_failed_workers}")


if __name__ == '__main__':
    flags = make_flags(from_argv=True)
    main(flags)
