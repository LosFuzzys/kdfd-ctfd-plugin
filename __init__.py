import json
import logging
from functools import wraps
from typing import Optional

import requests
from flask import Blueprint, Flask, Response, render_template, request

from CTFd.cache import cache, clear_config
from CTFd.models import Challenges, Configs, Solves, db
from CTFd.plugins import register_plugin_assets_directory
from CTFd.plugins.challenges import CHALLENGE_CLASSES, BaseChallenge, CTFdStandardChallenge
from CTFd.plugins.dynamic_challenges import DynamicChallenge, DynamicValueChallenge
from CTFd.plugins.migrations import upgrade
from CTFd.utils import config as ctf_config
from CTFd.utils import get_config, set_config
from CTFd.utils.decorators import (
    admins_only,
    during_ctf_time_only,
    ratelimit,
    require_verified_emails,
)
from CTFd.utils.decorators.visibility import check_challenge_visibility
from CTFd.utils.plugins import register_script
from CTFd.utils.user import get_current_user

kdfd = Blueprint("kdfd", __name__, template_folder="templates")

patched_challenge_classes = {}


def check_enabled(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        enabled = get_config(f'kdfd_enabled')
        if not enabled:
            raise FailureException('Instances not enabled')
        return func(*args, **kwargs)
    return wrapper


def handle_exceptions(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except FailureException as e:
            logging.exception('something went wrong')
            return {"success": False, "message": str(e)}, 500
        except json.decoder.JSONDecodeError as e:
            logging.exception('something went wrong')
            return {"success": False, "message": "The upstream server is responding incorrectly. Please contact an administrator."}, 500
        except requests.exceptions.ConnectionError as e:
            logging.exception('something went wrong')
            return {"success": False, "message": "The upstream server is not responding. Please contact an administrator."}, 500
        except Exception:
            logging.exception('something went wrong')
            return {"success": False}, 500
    return wrapper


class FailureException(Exception):
    pass


def success(**kwargs):
    return {"success": True, **kwargs}


def get_global_config():
    return {
        "kdfd_ctf_name": get_config("kdfd_ctf_name"),
        "kdfd_controller_url": get_config("kdfd_controller_url"),
        "kdfd_controller_timeout": get_config("kdfd_controller_timeout", default=5),
        "kdfd_controller_auth_token": get_config("kdfd_controller_auth_token"),
    }


def get_challenge_config(challenge):
    config = get_global_config()
    for challenge_topic in challenge.topics:
        topic = challenge_topic.topic.value
        if topic.startswith('kdfd_') and '=' in topic:
            key, value = topic.split('=', maxsplit=1)
            config[key] = value
    return config



def get_overwritable_config(challenge, name, default=None):
    return getattr(challenge, name, get_config(name, default=default))


@kdfd.route("/admin/kdfd", methods=["GET", "POST"])
@admins_only
def config():
    # Clear the config cache so that we don't get stale values
    clear_config()

    configs = Configs.query.all()
    configs = {c.key: get_config(c.key) for c in configs}

    return render_template("kdfd_config.html", **configs)


@kdfd.route("/api/v1/kdfd/challenge/<challenge_id>", methods=['GET', 'PUT', 'DELETE'])
@ratelimit(method="POST", limit=5, interval=10)
@check_challenge_visibility
@during_ctf_time_only
@require_verified_emails
@handle_exceptions
@check_enabled
def update_challenge(challenge_id):
    user = get_current_user()
    challenge = Challenges.query.filter_by(id=challenge_id).first_or_404()

    config = get_challenge_config(challenge)

    ctf_name = config['kdfd_ctf_name']
    server = config['kdfd_controller_url']
    timeout = int(config['kdfd_controller_timeout'])
    auth_token = config['kdfd_controller_auth_token']
    app_name = config['kdfd_app_name']

    if not (ctf_name and server and timeout and auth_token and app_name):
        raise FailureException("Challenge not configured")

    instance_id = user.id
    cookies = {'auth-token': auth_token}

    if request.method == 'GET':
        res = requests.get(f'{server}/api/v1/ctfs/{ctf_name}/apps/{app_name}/instances/{instance_id}',
                           cookies=cookies, timeout=timeout)
        logging.error(res.text)
        res = res.json()
        if not res['success']:
            return {"success": False, "msg": "Could not retrieve instance state", "res": res}
        if not res['instance']:
            return {"success": True, "response": res, "active": False}
        expiry = res["instance"]["expiry"]
    elif request.method == 'PUT':
        res = requests.put(f'{server}/api/v1/ctfs/{ctf_name}/apps/{app_name}/instances/{instance_id}',
                           cookies=cookies, timeout=timeout)
        logging.error(res.text)
        res = res.json()
        if not res['success']:
            return {"success": False, "response": res}
        expiry = res["instance"]["expiry"]
    else:  # request.method == 'DELETE':
        res = requests.delete(f'{server}/api/v1/ctfs/{ctf_name}/apps/{app_name}/instances/{instance_id}',
                              cookies=cookies, timeout=timeout)
        logging.error(res.text)
        res = res.json()
        if not res['success']:
            return {"success": False, "response": res}
        return {"success": True, "active": False, "response": res}
    # TODO challenge_name = parse(challenge.comments) (parse slug from db)
    challenge_name = 'chal1'
    res = requests.get(f'{server}/api/v1/ctfs/{ctf_name}/apps/{app_name}/instances/{instance_id}/challenge',
                       params={'challenge_name': challenge_name}, cookies=cookies, timeout=timeout)
    logging.error(res.text)
    res = res.json()
    if not res['success']:
        return {"success": False, "msg": "Could not retrieve challenge connection info.", "res": res}
    connection_info_html = res.get("connection_info_html", 'error')
    return {"success": True, "active": True, "response": res, "connection_info_html": connection_info_html, "expiry": expiry}


@kdfd.route("/plugins/kdfd/<class_name>/inject.js")
#@cache.cached() # TODO reenable
def inject(class_name):
    view = patched_challenge_classes[class_name]["view.js"]
    res = requests.get(f'http://0:8000/{view}')
    # TODO does this need to be a template?
    js_content = f'{res.text}\n\n{render_template("kdfd_challenge_view.js")}'
    return Response(js_content, mimetype='application/javascript; charset=utf-8')


def patch_challenge_classes():
    # TODO might be necessary to wait until all challanges have been loaded
    for class_name, challenge_class in CHALLENGE_CLASSES.items():
        try:
            patched_challenge_classes[class_name] = {
                "view.js": challenge_class.scripts["view"]
            }
            challenge_class.scripts["view"] = f'/plugins/kdfd/{class_name}/inject.js'
        except Exception:
            logging.warn('could not patch challenge class {class_name}')


def load(app):
    register_plugin_assets_directory(
        app, base_path="/plugins/kdfd-ctfd-plugin/static/")
    app.register_blueprint(kdfd)
    patch_challenge_classes()
