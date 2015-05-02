from collections import OrderedDict
from .exceptions import JSONConfigException
import numbers


_undefined = object()


class JSONConfigQueryError(JSONConfigException):
    """
    The base class of every exceptions thrown by this library during config queries.
    """
    def __init__(self, config_node, message):
        """
        :param config_node: An instance of one of the subclasses of _ConfigNode.
        """
        self.config_node = config_node
        self.line, self.column = node_location(config_node)
        self.line += 1
        self.column += 1
        message += ' [line=%s;col=%s]' % (self.line, self.column)
        super(JSONConfigQueryError, self).__init__(message)


class JSONConfigValueMapperError(JSONConfigQueryError):
    """
    This is raised when someone fetches a value by specifying the "mapper" parameter
    and the mapper function raises an exception. That exception is converted into this one.
    """
    def __init__(self, config_node, mapper_exception):
        """
        :param config_node:  An instance of one of the subclasses of _ConfigNode.
        :param mapper_exception: The exception instance that was raised during conversion.
        It can be anything...
        """
        super(JSONConfigValueMapperError, self).__init__(config_node,
                                                         'Error converting json value: ' +
                                                         str(mapper_exception))
        self.mapper_exception = mapper_exception


class JSONConfigValueNotFoundError(JSONConfigQueryError):
    """
    Raised when the user tries to fetch a value that doesn't exist in the config.
    """
    def __init__(self, value_not_found):
        """
        :param value_not_found: A ValueNotFoundNode instance. Let's say that you query the
        config.servers[1].ip_address() value from the config but the config.servers array
        has only one item. In this case a JSONConfigValueNotFoundError is raised and
        value_not_found._parent_config_node is set to config.servers (that is the last existing
        component from our query path) and self.relative_path will be '[1].ip_address'.
        This way the error location points to the config.servers node and the error message
        says that you wanted to query it with the '[1].ip_address' relative_path that doesn't
        exist.
        :type value_not_found: ValueNotFoundNode
        """
        self.value_not_found = value_not_found
        path = []
        for component in value_not_found._missing_query_path:
            if isinstance(component, numbers.Integral):
                path.append('[%s]' % component)
            else:
                path.append('.' + component)
        self.relative_path = ''.join(path)
        # TODO: improve the error message: it is possible to do so based on the info we have
        message = 'Required config node not found. Missing query path: %s'\
            ' (relative to error location)' % self.relative_path
        super(JSONConfigValueNotFoundError, self).__init__(value_not_found._parent_config_node,
                                                           message)


class JSONConfigNodeTypeError(JSONConfigQueryError):
    """
    This error is raised when you try to handle a config node by assuming its type
    to be something else than its actual type. For example you are trying to iterate
    over the key-value pairs of a value that is not json object.
    """
    def __init__(self, config_node, expected_type, error_message=None):
        """
        :param config_node: An instance of one of the subclasses of _ConfigNode.
        :param expected_type: The expected type or a tuple/list of expected types.
        """
        found_type_name = config_node.__class__.__name__
        if not isinstance(expected_type, (list, tuple)):
            expected_type = (expected_type,)
        expected_names = [t.__name__ for t in expected_type]
        message = 'Expected a %s but found %s.' % (' or '.join(expected_names), found_type_name)
        if error_message is not None:
            message += ' %s' % (error_message,)
        super(JSONConfigNodeTypeError, self).__init__(config_node, message)


class JSONValueMapper(object):
    def __call__(self, json_value):
        raise NotImplementedError()


def _process_value_fetcher_call_args(args):
    """
    This function processes the incoming varargs of ValueNotFoundNode.__call__() and
    _ConfigNode.__call__().
    :param args: A list or tuple containing positional function call arguments. The optional
    arguments we expect are the following: An optional default value followed by zero or more
    JSONValueMapper instances.
    :return: (default_value, list_or_tuple_of_JSONValueMapper_instances)
    The default_value is _undefined if it is not present and the second item of the tuple is
    an empty tuple/list if there are not JSONValueMapper instances.
    """
    if not args:
        return _undefined, ()

    if isinstance(args[0], JSONValueMapper):
        default = _undefined
        mappers = args
    else:
        default = args[0]
        mappers = args[1:]

    for mapper in mappers:
        if not isinstance(mapper, JSONValueMapper):
            raise TypeError('%r in\'t a JSONValueMapper instance!' % (mapper,))

    return default, mappers


class ValueNotFoundNode(object):
    def __init__(self, parent_config_node, missing_query_path):
        """
        If the user issues a config query like config.servers[2].ip_address but there is only
        one server in the config (so config.servers[2] doesn't exist) then the existing part
        of the query path is config.servers and the missing part is [2].ip_address. In this case
        parent_config_node will be the last node of the existing part, in this case the servers
        array, and the missing_query_path is [2].ip_address.
        :param parent_config_node: The last existing config_node on the query path issued
        by the user. missing_query_path is the non-existing part of the query path and it
        is relative to the parent_config_node.
        :param missing_query_path: The non-existing part (suffix) of the query path issued
        by the user. This is relative to parent_config_node.
        """
        self._parent_config_node = parent_config_node
        self._missing_query_path = missing_query_path

    def __call__(self, *args):
        """
        This function expects the exact same parameters as _ConfigNode.__call__():
        An optional default value followed by zero or more JSONValueMapper instances.
        Since this is a not-found-node we know that this wrapper object doesn't contain any
        json value so the mapper arguments are ignored.
        If a default value is provided then we return it otherwise we raise an exception since
        the user tries to fetch a required value that isn't in the config file.
        """
        default, mappers = _process_value_fetcher_call_args(args)
        if default is _undefined:
            raise JSONConfigValueNotFoundError(self)
        return default

    def __getattr__(self, item):
        return self.__getitem__(item)

    def __getitem__(self, item):
        return ValueNotFoundNode(self._parent_config_node, self._missing_query_path + [item])

    def __len__(self):
        raise JSONConfigValueNotFoundError(self)

    def __iter__(self):
        raise JSONConfigValueNotFoundError(self)


class _ConfigNode(object):
    """
    Base class for the actual classes whose instances build up the config
    object hierarchy wrapping the actual json objects/arrays/scalars.
    Note that this class and its subclasses should have only private members
    with names that start with '_' because the keys in the json config
    can be accessed using the member operator (dot) and the members of the
    config node class instances should not conflict with the keys in the
    config files.
    """

    def __init__(self, line, column):
        """
        :param line: Zero based line number. (Add 1 for human readable error reporting).
        :param column: Zero based column number. (Add 1 for human readable error reporting).
        """
        super(_ConfigNode, self).__init__()
        self._line = line
        self._column = column

    def __call__(self, *args):
        """
        This function will fetch the wrapped json value from this wrapper config node.
        We expect the following optional arguments:
        An optional default value followed by zero or more JSONValueMapper instances.
        Since this is not a not-found-node we know that there is a wrapped json value so the
        default value is ignored. If we have JSONValueMapper instances then we apply them to
        the wrapped json value in left-to-right order before returning the json value.
        """
        default, mappers = _process_value_fetcher_call_args(args)
        value = self._fetch_unwrapped_value()
        try:
            for mapper in mappers:
                value = mapper(value)
        except Exception as e:
            raise JSONConfigValueMapperError(self, e)
        return value

    def _fetch_unwrapped_value(self):
        raise NotImplementedError()


class ConfigJSONScalar(_ConfigNode):
    def __init__(self, value, line, column):
        super(ConfigJSONScalar, self).__init__(line, column)
        self.value = value

    def __getattr__(self, item):
        return ValueNotFoundNode(self, [item])

    def __getitem__(self, index):
        return ValueNotFoundNode(self, [index])

    def __len__(self):
        raise JSONConfigNodeTypeError(
            self,
            (ConfigJSONObject, ConfigJSONArray),
            'You are trying to get the length of a scalar value.'
        )

    def __iter__(self):
        raise JSONConfigNodeTypeError(
            self,
            (ConfigJSONObject, ConfigJSONArray),
            'You are trying to iterate a scalar value.'
        )

    def __repr__(self):
        return '%s(value=%r, line=%r, column=%r)' % (self.__class__.__name__,
                                                     self.value, self._line, self._column)

    def _fetch_unwrapped_value(self):
        return self.value


class ConfigJSONObject(_ConfigNode):
    def __init__(self, line, column):
        super(ConfigJSONObject, self).__init__(line, column)
        self._dict = OrderedDict()

    def __getattr__(self, item):
        return self.__getitem__(item)

    def __getitem__(self, item):
        if item in self._dict:
            return self._dict[item]
        return ValueNotFoundNode(self, [item])

    def __contains__(self, item):
        return item in self._dict

    def __len__(self):
        return len(self._dict)

    def __iter__(self):
        return iter(self._dict.items())

    def __repr__(self):
        return '%s(len=%r, line=%r, column=%r)' % (self.__class__.__name__,
                                                   len(self), self._line, self._column)

    def _fetch_unwrapped_value(self):
        return {key: node._fetch_unwrapped_value() for key, node in self._dict.items()}

    def _insert(self, key, value):
        self._dict[key] = value


class ConfigJSONArray(_ConfigNode):
    def __init__(self, line, column):
        super(ConfigJSONArray, self).__init__(line, column)
        self._list = []

    def __getattr__(self, item):
        return ValueNotFoundNode(self, [item])

    def __getitem__(self, index):
        if isinstance(index, numbers.Integral):
            if index < 0:
                index += len(self._list)
            if 0 <= index < len(self._list):
                return self._list[index]
        return ValueNotFoundNode(self, [index])

    def __len__(self):
        return len(self._list)

    def __iter__(self):
        return iter(self._list)

    def __repr__(self):
        return '%s(len=%r, line=%r, column=%r)' % (self.__class__.__name__,
                                                   len(self), self._line, self._column)

    def _fetch_unwrapped_value(self):
        return [node._fetch_unwrapped_value() for node in self._list]

    def _append(self, item):
        self._list.append(item)


def node_location(config_node):
    if isinstance(config_node, _ConfigNode):
        return config_node._line, config_node._column
    if isinstance(config_node, ValueNotFoundNode):
        raise JSONConfigValueNotFoundError(config_node)
    raise TypeError('Expected a config node but received a %s instance.' %
                    type(config_node).__name__)


def node_exists(config_node):
    """ Returns True if the specified config node
    refers to an existing config entry. """
    return isinstance(config_node, _ConfigNode)


def node_is_object(config_node):
    """ Returns True if the specified config node refers
    to an existing config entry that is a json object (dict). """
    return isinstance(config_node, ConfigJSONObject)


def node_is_array(config_node):
    """ Returns True if the specified config node refers
    to an existing config entry that is a json array (list). """
    return isinstance(config_node, ConfigJSONArray)


def node_is_scalar(config_node):
    """ Returns True if the specified config node refers to an existing config
    entry that isn't a json object (dict) or array (list) but something else. """
    return isinstance(config_node, ConfigJSONScalar)


def _guarantee_node_class(config_node, node_class):
    if isinstance(config_node, node_class):
        return config_node
    if isinstance(config_node, ValueNotFoundNode):
        raise JSONConfigValueNotFoundError(config_node)
    if isinstance(config_node, _ConfigNode):
        raise JSONConfigNodeTypeError(config_node, node_class)
    raise TypeError('Expected a %s or %s instance but received %s.' % (
        _ConfigNode.__name__, ValueNotFoundNode.__name__, config_node.__class__.__name__))


def ensure_exists(config_node):
    return _guarantee_node_class(config_node, _ConfigNode)


def expect_object(config_node):
    return _guarantee_node_class(config_node, ConfigJSONObject)


def expect_array(config_node):
    return _guarantee_node_class(config_node, ConfigJSONArray)


def expect_scalar(config_node):
    return _guarantee_node_class(config_node, ConfigJSONScalar)