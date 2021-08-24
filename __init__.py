import json
import logging
from functools import wraps
from typing import Optional

import requests
from flask import Blueprint, Flask, Response, render_template, request

from CTFd.cache import cache, clear_config
from CTFd.models import Challenges, Configs
from CTFd.plugins import register_plugin_assets_directory
from CTFd.plugins.challenges import CHALLENGE_CLASSES, BaseChallenge
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


def get_challenge_data(challenge_id: str) -> dict:
    challenge = Challenges.query.filter_by(id=challenge_id).first_or_404()
    for comment in challenge.comments:
        try:
            data = json.loads(comment.content)
            if data["kdfd-ctfd-plugin-data"]:
                return data["kdfd-ctfd-plugin-data"]
        except Exception:
            logging.exception('oh no')# TODO remove
            pass
    raise FailureException('Challenge configuration not found')


def get_overwritable_config(challenge_data: dict, name, default=None):
    return challenge_data.get(name, get_config(f'kdfd_{name}', default=default))


@kdfd.route("/admin/kdfd", methods=["GET", "POST"])
#@admins_only
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
    challenge_data = get_challenge_data(challenge_id)

    ctf_name = get_overwritable_config(challenge_data, 'ctf_name')
    server = get_overwritable_config(challenge_data, 'controller_url')
    timeout = get_overwritable_config(challenge_data, 'controller_timeout', default=5)
    auth_token = get_overwritable_config(challenge_data, 'controller_auth_token')
    app_name = challenge_data['app_name']

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
    #register_plugin_assets_directory(
    #    app, base_path="/plugins/kdfd-ctfd-plugin/assets/")
    register_plugin_assets_directory(
        app, base_path="/plugins/kdfd-ctfd-plugin/static/")
    #register_script('test')  # TODO set correct url
    app.register_blueprint(kdfd)
    patch_challenge_classes()
