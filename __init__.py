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
from CTFd.plugins.dynamic_challenges import DynamicValueChallenge
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


class KdfdChallengeModel(Challenges):
    __mapper_args__ = {"polymorphic_identity": "kdfd"}
    id = db.Column(
        db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"), primary_key=True
    )

    kdfd_data = db.Column('kdfd_data', db.JSON)

    def __init__(self, *args, **kwargs):
        super(KdfdChallengeModel, self).__init__(**kwargs)
        self.value = kwargs["initial"]


class KdfdChallenge(BaseChallenge):
    id = "kdfd"  # Unique identifier used to register challenges
    name = "kdfd"  # Name of a challenge type
    templates = {  # Templates used for each aspect of challenge editing & viewing
        "create": "/plugins/kdfd-ctfd-plugin/assets/create.html",
        "update": "/plugins/kdfd-ctfd-plugin/assets/update.html",
        "view": "/plugins/kdfd-ctfd-plugin/assets/view.html",
    }
    scripts = {  # Scripts that are loaded when a template is loaded
        "create": "/plugins/kdfd-ctfd-plugin/assets/create.js",
        "update": "/plugins/kdfd-ctfd-plugin/assets/update.js",
        "view": "/plugins/kdfd-ctfd-plugin/assets/view.js",
    }
    # Route at which files are accessible. This must be registered using register_plugin_assets_directory()
    route = "/plugins/kdfd-ctfd-plugin/assets/"
    challenge_model = KdfdChallengeModel


    @classmethod
    def get_wrapped_type(cls, challenge):
        wrapped_class = CHALLENGE_CLASSES[challenge.kdfd_data['wrapped_type']]
        wrapped_data = challenge.kdfd_data['wrapped_data']
        for key, value in wrapped_data.items():
            setattr(challenge, key, value)
        return wrapped_class, wrapped_data

    @classmethod
    def create(cls, request):
        """
        This method is used to process the challenge creation request.

        :param request:
        :return:
        """
        data = request.form or request.get_json()

        wrapped_type = data.get('wrapped_type', 'standard')

        if 'wrapped_type' not in kdfd_data:
            kdfd_data['wrapped_type'] = 'standard'

        if 'wrapped_data' not in kdfd_data:
            kdfd_data['wrapped_data'] = {}

        data['kdfd_data'] = {
            "wrapped_type": wrapped_type
        }

        challenge = cls.challenge_model(**data)

        db.session.add(challenge)
        db.session.commit()

        return challenge

    @classmethod
    def read(cls, challenge):
        """
        This method is in used to access the data of a challenge in a format processable by the front end.

        :param challenge:
        :return: Challenge object, data dictionary to be returned to the user
        """
        wrapped_class, wrapped_data = cls.get_wrapped_type(challenge)
        data = wrapped_class.read(challenge)
        data_overwrite = {
            "type": challenge.type,
            "type_data": {
                "id": cls.id,
                "name": cls.name,
                "templates": {
                    "create": "/plugins/kdfd-ctfd-plugin/assets/create.html",
                    "update": "/plugins/kdfd-ctfd-plugin/assets/update.html",
                    "view": "/plugins/kdfd-ctfd-plugin/assets/view.html",
                },
                "scripts": {
                    "create": "/plugins/kdfd-ctfd-plugin/assets/create.js",
                    "update": "/plugins/kdfd-ctfd-plugin/assets/update.js",
                    "view": "/plugins/kdfd-ctfd-plugin/assets/view.js",
                },
            },
        }
        for key, value in data_overwrite.items():
            data[key] = value
        return data

    @classmethod
    def update(cls, challenge, request):
        """
        This method is used to update the information associated with a challenge. This should be kept strictly to the
        Challenges table and any child tables.

        :param challenge:
        :param request:
        :return:
        """
        data = request.form or request.get_json()
        for attr, value in data.items():
            setattr(challenge, attr, value)

        db.session.commit()
        return challenge

    @classmethod
    def delete(cls, challenge):
        """
        This method is used to delete the resources used by a challenge.

        :param challenge:
        :return:
        """
        wrapped_class, wrapped_data = cls.get_wrapped_type(challenge)
        wrapped_class.delete(challenge)

    @classmethod
    def attempt(cls, challenge, request):
        """
        This method is used to check whether a given input is right or wrong. It does not make any changes and should
        return a boolean for correctness and a string to be shown to the user. It is also in charge of parsing the
        user's input from the request itself.

        :param challenge: The Challenge object from the database
        :param request: The request the user submitted
        :return: (boolean, string)
        """
        wrapped_class, wrapped_data = cls.get_wrapped_type(challenge)
        wrapped_class.attempt(challenge, request)

    @classmethod
    def solve(cls, user, team, challenge, request):
        """
        This method is used to insert Solves into the database in order to mark a challenge as solved.

        :param team: The Team object from the database
        :param chal: The Challenge object from the database
        :param request: The request the user submitted
        :return:
        """
        wrapped_class, wrapped_data = cls.get_wrapped_type(challenge)
        wrapped_class.solve(user, team, challenge, request)

    @classmethod
    def fail(cls, user, team, challenge, request):
        """
        This method is used to insert Fails into the database in order to mark an answer incorrect.

        :param team: The Team object from the database
        :param chal: The Challenge object from the database
        :param request: The request the user submitted
        :return:
        """
        data = request.form or request.get_json()
        submission = data["submission"].strip()
        wrong = Fails(
            user_id=user.id,
            team_id=team.id if team else None,
            challenge_id=challenge.id,
            ip=get_ip(request),
            provided=submission,
        )
        db.session.add(wrong)
        db.session.commit()


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

    ctf_name = get_overwritable_config(challenge, 'kdfd_ctf_name')
    server = get_overwritable_config(challenge, 'kdfd_controller_url')
    timeout = get_overwritable_config(
        challenge, 'kdfd_controller_timeout', default=5)
    auth_token = get_overwritable_config(
        challenge, 'kdfd_controller_auth_token')
    app_name = challenge.kdfd_app_name

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


#def patch_challenge_classes():
#    # TODO might be necessary to wait until all challanges have been loaded
#    for class_name, challenge_class in CHALLENGE_CLASSES.items():
#        try:
#            patched_challenge_classes[class_name] = {
#                "view.js": challenge_class.scripts["view"]
#            }
#            challenge_class.scripts["view"] = f'/plugins/kdfd/{class_name}/inject.js'
#        except Exception:
#            logging.warn('could not patch challenge class {class_name}')


def load(app):
    CHALLENGE_CLASSES["kdfd"] = KdfdChallenge
    register_plugin_assets_directory(
        app, base_path="/plugins/kdfd-ctfd-plugin/assets/")
    register_plugin_assets_directory(
        app, base_path="/plugins/kdfd-ctfd-plugin/static/")
    app.register_blueprint(kdfd)
    #patch_challenge_classes()
