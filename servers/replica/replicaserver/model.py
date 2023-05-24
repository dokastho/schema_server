import replicaserver
import sqlite3
import flask
from random import randint
from pydrpc.drpc_client import *


def dict_factory(cursor, row):
    """Convert database row objects to a dictionary keyed on column name.

    This is useful for building dictionaries which are then used to render a
    template.  Note that this would be inefficient for large queries.
    """
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def get_db(table_id: str):
    """Open a new database connection.

    Flask docs:
    https://flask.palletsprojects.com/en/1.0.x/appcontext/#storing-data
    """
    if 'sqlite_db' not in flask.g:
        db_path = replicaserver.app.config['UPLOAD_FOLDER']
        db_filename = db_path / table_id
        flask.g.sqlite_db = sqlite3.connect(str(db_filename))
        flask.g.sqlite_db.row_factory = dict_factory
        # Foreign keys have to be enabled per-connection.  This is an sqlite3
        # backwards compatibility thing.
        flask.g.sqlite_db.execute("PRAGMA foreign_keys = ON")
    return flask.g.sqlite_db


def get_table_uuid(table):
    """Get table uuid."""
    
    # set up connection with translational table
    db_path = replicaserver.app.config['UPLOAD_FOLDER']
    db_filename = db_path / "schemas.sqlite3"
    connection = sqlite3.connect(str(db_filename))
    connection.row_factory = dict_factory
    # Foreign keys have to be enabled per-connection.  This is an sqlite3
    # backwards compatibility thing.
    connection.execute("PRAGMA foreign_keys = ON")

    # fetch uuid
    cur = connection.execute(
        "SELECT fileid FROM tables WHERE name = ?",
        (table,)
    )
    uuid = cur.fetchone()
    if len(uuid) == 0:
        flask.abort(404)
    
    # close
    connection.commit()
    connection.close()
    
    return uuid['fileid']


@replicaserver.app.teardown_appcontext
def close_db(error):
    """Close the database at the end of a request.

    Flask docs:
    https://flask.palletsprojects.com/en/1.0.x/appcontext/#storing-data
    """
    assert error or not error  # Needed to avoid superfluous style error
    sqlite_db = flask.g.pop('sqlite_db', None)
    if sqlite_db is not None:
        sqlite_db.commit()
        sqlite_db.close()


def apply_op(Op: replicaserver.d3b_op):
    """Apply a database operation returned from the paxos process."""
    replicaserver.seq_lock.acquire()

    # perform database operation
    body = Op.data
    table_uuid = get_table_uuid(body["table"])
    connection = get_db(table_uuid)
    cur = connection.execute(body["query"], body["args"])
    data = cur.fetchall()
    
    # if there's a media upload, get blob & save it
    # if replicaserver.MEDIA_MASK & Op.flags != 0:
    #     # upload
    #     if replicaserver.MEDIA_UPLOAD & Op.flags != 0:
    #         blob = flask.request.files.get('file')

    #         # save file
    #         path = replicaserver.app.config["UPLOAD_FOLDER"]/file_id
    #         blob.save(path)
    #         pass
    #     # delete
    #     elif replicaserver.MEDIA_DELETE & Op.flags != 0:
    #         # delete file
    #         os.remove(os.path.join(
    #             replicaserver.app.config['UPLOAD_FOLDER'],
    #             file_id)
    #         )
    #         pass

    #     pass

    replicaserver.seq_lock.release()
    return data


def add_op(Op: replicaserver.d3b_op):
    """perform db updates until after request is returned"""

    # want random host
    dh = drpc_host()
    hosts = replicaserver.app.config["PAXOS_HOSTS"]
    host_idx = randint(0, len(hosts) - 1)
    dh.hostname = hosts[host_idx]
    dh.port = replicaserver.app.config["PAXOS_PORTS"][host_idx]

    # request
    d3b_req = Op

    # reply
    d3b_rep = replicaserver.d3b_op()

    # RPC
    req = drpc_arg_wrapper(d3b_req)
    rep = drpc_arg_wrapper(d3b_rep)
    m = drpc_msg()
    m.req = req
    m.rep = rep
    m.target = replicaserver.app.config["PAXOS_ENDPOINT"]

    c = drpc_client()
    logged = False
    data = dict()
    while not logged:
        c.Call(dh, m)
        logged = True
        data = apply_op(m.rep.args)

        # continue logging if the value returned isn't the one we requested to log
        if m.rep.args.seed != m.req.args.seed:
            logged = False
            continue

        pass
    return data
